"""Shared trust-artifact logic for `continuum evidence` and `continuum pr-packet`.

Both commands inspect everything Continuum knows about a task and apply the same
deterministic risk heuristics, so the gathering logic lives here as one source of
truth. Nothing in this module trusts an agent self-report: every field is read
back from the recorded event log, file claims and the worktree gate records.
"""

from __future__ import annotations

from typing import Any

from .core import MemoryStore
from .worktrees import WorktreeError, WorktreeManager

# Event kinds worth surfacing as "commands run / activity" for a task. Filtering to
# these keeps the evidence record focused on actions rather than every status poll.
RELEVANT_EVENT_KINDS = {
    "task_created",
    "task_assigned",
    "files_claimed",
    "task_status",
    "worktree_created",
    "worktree_tests",
    "worktree_review",
    "worktree_merged",
    "worktree_discarded",
    "model_ask",
}


class EvidenceError(RuntimeError):
    pass


def gather_evidence(store: MemoryStore, task_ref: str) -> dict[str, Any]:
    """Aggregate task, claims, changes, gates and deterministic risks into one record.

    Raises EvidenceError only for a real error (unknown task); an incomplete
    evidence picture (no worktree, missing gate) is reported in the record, not raised.
    """
    task = store.get_task(task_ref)
    if not task:
        raise EvidenceError(f"Unknown task: {task_ref}")

    task_id = task["task_id"]
    claimed_files = sorted(lock["path"] for lock in task.get("locked_files", []))
    events = _task_events(store, task_id)

    manager = WorktreeManager(store)
    worktree: dict[str, Any] | None = None
    changed_files: list[str] = []
    diff_summary = ""
    head_sha: str | None = None
    worktree_note = "no worktree"
    if task_id in manager.list_records_index():
        try:
            worktree = manager.diff(task_id)
            changed_files = list(worktree.get("changed_files", []))
            diff_summary = worktree.get("diff_summary") or ""
            head_sha = manager.branch_head(task_id)
            worktree_note = "worktree present"
        except WorktreeError as error:
            worktree = manager.record(task_id)
            worktree_note = f"worktree error: {error}"

    test_gate = {
        "result": (worktree or {}).get("test_result"),
        "note": (worktree or {}).get("completion_note"),
        "sha": (worktree or {}).get("test_sha"),
    }
    review_gate = {
        "result": (worktree or {}).get("review_status"),
        "note": (worktree or {}).get("review_note"),
        "sha": (worktree or {}).get("review_sha"),
    }

    risks = _risks(claimed_files, changed_files, test_gate, review_gate, head_sha, worktree)
    next_action = _next_action(task, test_gate, review_gate, risks, worktree)
    contributions = _contributions(manager, task, worktree)

    return {
        "task_id": task_id,
        "title": task["title"],
        "status": task["status"],
        "agent": task.get("agent"),
        "mode": task["mode"],
        "summary": task.get("summary"),
        "claimed_files": claimed_files,
        "changed_files": changed_files,
        "diff_summary": diff_summary,
        "worktree": worktree,
        "worktree_note": worktree_note,
        "branch": (worktree or {}).get("branch") or task.get("branch"),
        "head_sha": head_sha,
        "events": events,
        "test_gate": test_gate,
        "review_gate": review_gate,
        "risks": risks,
        "next_action": next_action,
        "contributions": contributions,
    }


def _contributions(
    manager: WorktreeManager, task: dict[str, Any], worktree: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """List per-lane agents for a scheduled parallel objective, else the task's agent.

    Reachability is best-effort: if the schedule record is unavailable we fall back
    to the single task agent rather than failing the evidence gather.
    """
    schedule_id = (worktree or {}).get("schedule_id")
    if schedule_id:
        try:
            schedule = next(
                (item for item in manager.schedules() if item["schedule_id"] == schedule_id),
                None,
            )
        except Exception:  # pragma: no cover - schedule store best-effort
            schedule = None
        if schedule:
            return [
                {"task_id": lane["task_id"], "role": lane.get("role"), "agent": lane.get("agent")}
                for lane in schedule["lanes"]
            ]
    return [{"task_id": task["task_id"], "role": None, "agent": task.get("agent")}]


def _task_events(store: MemoryStore, task_id: str) -> list[dict[str, Any]]:
    """Pull recorded events whose payload references this task, oldest first."""
    events = []
    for event in store.recent_events(500):
        if event["kind"] not in RELEVANT_EVENT_KINDS:
            continue
        payload = event["payload"]
        if str(payload.get("task_id", "")).upper() != task_id:
            continue
        detail = payload.get("summary") or payload.get("title") or payload.get("status") or ""
        events.append({"created_at": event["created_at"], "kind": event["kind"], "detail": detail})
    return events


def covered(path: str, claims: set[str]) -> bool:
    """Whether a changed file falls inside a claim.

    A lane is often claimed as a directory, so matching on the exact path alone
    reported every nested file as an out-of-scope edit and put a risk on
    evidence, flight records and ROI for work that was in scope all along.

    Segment by segment rather than by string prefix, because `src` must cover
    `src/app.py` without also covering `src-other/x.py`. Separators are
    normalized so a claim recorded on Windows still matches a path reported by
    Git.
    """
    def parts(value: str) -> list[str]:
        return [piece for piece in value.replace("\\", "/").strip("/").split("/") if piece]

    changed = parts(path)
    for claim in claims:
        wanted = parts(claim)
        if wanted and changed[: len(wanted)] == wanted:
            return True
    return False


def _risks(
    claimed_files: list[str],
    changed_files: list[str],
    test_gate: dict[str, Any],
    review_gate: dict[str, Any],
    head_sha: str | None,
    worktree: dict[str, Any] | None,
) -> list[str]:
    risks: list[str] = []
    claim_set = set(claimed_files)
    if claim_set:
        out_of_scope = sorted(path for path in changed_files if not covered(path, claim_set))
        for path in out_of_scope:
            risks.append(f"Out-of-scope edit: `{path}` was changed but not claimed.")
    if test_gate["result"] != "PASS":
        risks.append("Missing or failing test gate.")
    if review_gate["result"] != "APPROVED":
        risks.append("Missing or unapproved review gate.")
    if worktree and head_sha:
        if test_gate["sha"] and test_gate["sha"] != head_sha:
            risks.append("Worktree changed after the test gate (test_sha != branch HEAD).")
        if review_gate["sha"] and review_gate["sha"] != head_sha:
            risks.append("Worktree changed after the review gate (review_sha != branch HEAD).")
    return risks


def _next_action(
    task: dict[str, Any],
    test_gate: dict[str, Any],
    review_gate: dict[str, Any],
    risks: list[str],
    worktree: dict[str, Any] | None,
) -> str:
    task_id = task["task_id"]
    if not worktree:
        return f"Create an isolated worktree with `continuum worktree create {task_id}` to gather change evidence."
    if test_gate["result"] != "PASS":
        return f"Record the test gate: `continuum worktree test-result {task_id} --pass --note \"<command/result>\"`."
    if review_gate["result"] != "APPROVED":
        return f"Record the review gate: `continuum worktree review {task_id} --approve --note \"<review>\"`."
    if risks:
        return "Resolve the listed risks before merging; re-record gates against the current HEAD if it changed."
    if worktree.get("status") == "MERGED":
        return "Evidence complete; worktree already merged."
    return f"Evidence complete; merge with `continuum worktree merge {task_id}`."


def render_packet(evidence: dict[str, Any]) -> str:
    """Render a reviewer-facing markdown PR packet from a gathered evidence record."""
    task_id = evidence["task_id"]
    lines: list[str] = []
    lines.append(f"# PR Packet: {task_id} {evidence['title']}")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Task: {task_id} {evidence['title']}")
    lines.append(f"- Status: {evidence['status']} ({evidence['mode']})")
    if evidence.get("summary"):
        lines.append(f"- Note: {evidence['summary']}")
    lines.append(f"- Branch: {evidence['branch'] or 'none'}")
    lines.append("")

    lines.append("## Changed Files")
    if evidence["changed_files"]:
        for path in evidence["changed_files"]:
            lines.append(f"- `{path}`")
        if evidence["diff_summary"]:
            lines.append("")
            lines.append("```")
            lines.append(evidence["diff_summary"])
            lines.append("```")
    else:
        lines.append(f"- No changed files ({evidence['worktree_note']}).")
    lines.append("")

    lines.append("## Agent Contributions")
    contributions = evidence.get("contributions") or [
        {"agent": evidence.get("agent") or "unassigned", "role": None, "task_id": task_id}
    ]
    for item in contributions:
        role = f" ({item['role']})" if item.get("role") else ""
        lines.append(f"- {item['task_id']}{role}: {item.get('agent') or 'unassigned'}")
    lines.append("")

    test_gate = evidence["test_gate"]
    review_gate = evidence["review_gate"]
    lines.append("## Test Evidence")
    lines.append(f"- Result: {test_gate['result'] or 'not recorded'}")
    lines.append(f"- Note: {test_gate['note'] or '-'}")
    lines.append(f"- Recorded against SHA: {test_gate['sha'] or '-'}")
    lines.append("")

    lines.append("## Review Evidence")
    lines.append(f"- Result: {review_gate['result'] or 'not recorded'}")
    lines.append(f"- Note: {review_gate['note'] or '-'}")
    lines.append(f"- Recorded against SHA: {review_gate['sha'] or '-'}")
    lines.append("")

    lines.append("## Known Risks")
    if evidence["risks"]:
        for risk in evidence["risks"]:
            lines.append(f"- {risk}")
    else:
        lines.append("- None detected by deterministic heuristics.")
    lines.append("")

    lines.append("## Rollback")
    branch = evidence["branch"]
    worktree = evidence.get("worktree") or {}
    if worktree.get("status") == "MERGED":
        lines.append(f"- Revert the merge commit: `git revert -m 1 <merge-commit-of {branch}>`.")
    elif branch:
        lines.append(f"- Discard the unmerged branch: `git branch -D {branch}` (or `continuum worktree discard {task_id} --force`).")
    else:
        lines.append("- No branch recorded; nothing to roll back.")
    lines.append("")

    lines.append(f"Next action: {evidence['next_action']}")
    lines.append("")
    return "\n".join(lines)
