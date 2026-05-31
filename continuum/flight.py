"""Replayable task run records for Agent Flight Recorder."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .context_intel import score_intel
from .core import MemoryStore, compact_text
from .evidence import EvidenceError, gather_evidence


class FlightRecordError(RuntimeError):
    pass


def gather_flight_record(store: MemoryStore, task_ref: str) -> dict[str, Any]:
    """Build a deterministic, replayable record for one task.

    The record intentionally reuses stored state instead of agent self-reports:
    task metadata, file claims, worktree gates, context packets, events and
    messages are read back from Continuum's local store.
    """
    try:
        evidence = gather_evidence(store, task_ref)
    except EvidenceError as error:
        raise FlightRecordError(str(error)) from error

    context = _context_snapshot(store, evidence)
    messages = _task_messages(store, evidence["task_id"])
    command_events = [
        event for event in evidence["events"]
        if event["kind"] in {"model_ask", "worktree_tests", "worktree_review", "worktree_merged", "worktree_discarded"}
    ]
    return {
        "task_id": evidence["task_id"],
        "objective": evidence["title"],
        "agent": evidence.get("agent") or "unassigned",
        "model": _model_label(evidence),
        "status": evidence["status"],
        "final_status": _final_status(evidence),
        "branch": evidence.get("branch"),
        "context_packet": context,
        "files_allowed": evidence["claimed_files"],
        "files_touched": evidence["changed_files"],
        "commands_run": command_events,
        "terminal_outputs": _terminal_excerpt(store, evidence["task_id"]),
        "test_evidence": evidence["test_gate"],
        "review_notes": evidence["review_gate"],
        "risks": evidence["risks"],
        "cost_estimate": _cost_estimate(context, messages),
        "handoff": _latest_handoff(store),
        "events": evidence["events"],
        "messages": messages,
        "next_action": evidence["next_action"],
    }


def render_flight_record(record: dict[str, Any]) -> str:
    lines = [
        f"# Agent Flight Record: {record['task_id']}",
        "",
        f"- Objective: {record['objective']}",
        f"- Agent: {record['agent']}",
        f"- Model/provider: {record['model']}",
        f"- Status: {record['status']} ({record['final_status']})",
        f"- Branch: {record['branch'] or 'none'}",
        f"- Context packet: {record['context_packet'].get('id') or '-'}",
        f"- Estimated context tokens: {record['context_packet'].get('estimated_tokens') or 0}",
        f"- Estimated total tokens: {record['cost_estimate']['estimated_tokens']}",
        "",
        "## Files",
        f"- Allowed: {', '.join(f'`{item}`' for item in record['files_allowed']) or 'none'}",
        f"- Touched: {', '.join(f'`{item}`' for item in record['files_touched']) or 'none'}",
        "",
        "## Commands And Events",
    ]
    if record["commands_run"]:
        for event in record["commands_run"]:
            lines.append(f"- {event['created_at']} `{event['kind']}` {event['detail']}")
    else:
        lines.append("- No command or gate events recorded.")
    lines.extend([
        "",
        "## Test Evidence",
        f"- Result: {record['test_evidence'].get('result') or 'not recorded'}",
        f"- Note: {record['test_evidence'].get('note') or '-'}",
        f"- SHA: {record['test_evidence'].get('sha') or '-'}",
        "",
        "## Review Evidence",
        f"- Result: {record['review_notes'].get('result') or 'not recorded'}",
        f"- Note: {record['review_notes'].get('note') or '-'}",
        f"- SHA: {record['review_notes'].get('sha') or '-'}",
        "",
        "## Risks",
    ])
    if record["risks"]:
        lines.extend(f"- {risk}" for risk in record["risks"])
    else:
        lines.append("- None detected.")
    lines.extend(["", f"Next action: {record['next_action']}", ""])
    return "\n".join(lines)


def _context_snapshot(store: MemoryStore, evidence: dict[str, Any]) -> dict[str, Any]:
    worktree = evidence.get("worktree") or {}
    context_path = worktree.get("context_path")
    text = ""
    if context_path and Path(context_path).exists():
        text = Path(context_path).read_text(encoding="utf-8", errors="replace")
    else:
        packet = store.context_packet(evidence.get("agent") or "coder", evidence["title"], "compact")
        text = packet["text"]
    try:
        score = score_intel(store, evidence["task_id"])
    except Exception:
        score = {}
    return {
        "id": context_path or f"{evidence['task_id']}:compact",
        "path": context_path,
        "estimated_tokens": score.get("estimated_tokens") or store.context_packet(
            evidence.get("agent") or "coder", evidence["title"], "compact"
        )["estimated_tokens"],
        "score": score,
        "preview": compact_text(text, 1_200),
    }


def _task_messages(store: MemoryStore, task_id: str) -> list[dict[str, Any]]:
    return [item for item in store.messages(limit=100) if item.get("task_id") == task_id]


def _model_label(evidence: dict[str, Any]) -> str:
    agent = evidence.get("agent")
    if agent:
        return str(agent)
    contributions = evidence.get("contributions") or []
    labels = sorted({str(item.get("agent")) for item in contributions if item.get("agent")})
    return ", ".join(labels) if labels else "unknown"


def _final_status(evidence: dict[str, Any]) -> str:
    if evidence.get("risks"):
        return "blocked"
    if evidence["test_gate"].get("result") == "PASS" and evidence["review_gate"].get("result") == "APPROVED":
        return "merge_ready" if (evidence.get("worktree") or {}).get("status") != "MERGED" else "merged"
    return "evidence_incomplete"


def _cost_estimate(context: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    message_tokens = sum(int(item.get("estimated_tokens") or 0) for item in messages)
    context_tokens = int(context.get("estimated_tokens") or 0)
    return {
        "estimated_tokens": context_tokens + message_tokens,
        "context_tokens": context_tokens,
        "message_tokens": message_tokens,
        "estimated_usd": None,
    }


def _terminal_excerpt(store: MemoryStore, task_id: str) -> list[dict[str, str]]:
    log_dir = store.state_dir / "session_logs"
    if not log_dir.exists():
        return []
    matches = []
    for path in sorted(log_dir.glob("*.log"))[-5:]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if task_id in text:
            matches.append({"path": str(path), "excerpt": compact_text(text, 1_200)})
    return matches


def _latest_handoff(store: MemoryStore) -> str:
    path = store.state_dir / "latest_handoff.md"
    if not path.exists():
        return ""
    return compact_text(path.read_text(encoding="utf-8", errors="replace"), 1_200)
