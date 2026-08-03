"""Measure what Continuum claims: that a fresh agent continues instead of restarting.

Three things get measured, each against a project whose state we control, so
every answer has a known-correct value:

1. Context size, per mode, against the raw history it replaces.
2. Handoff fidelity across four arms: Continuum injecting the context, the
   agent reading `.continuum/` itself with no injection, no project memory at
   all, and a conflict case where the injected text and the files disagree.
3. The LongMemEval categories: knowledge update, temporal ordering,
   multi-session recall and abstention.
4. Delegation cost, in wall time and tokens, for one agent to consult another.

This calls real agent CLIs, so it needs them installed and signed in, and it
spends quota. Results and caveats live in docs/benchmarks.md.

Usage: python benchmarks/agent_memory_bench.py [--trials N] [--agents claude,codex]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import statistics
import subprocess
import tempfile
import sys
import time
from pathlib import Path

from continuum.core import MemoryStore, estimate_tokens  # noqa: E402
from continuum.delegation import DelegationError, ask  # noqa: E402

# The recorded project state. Every probe below is answerable from this and
# from nothing else, so a correct answer proves the handoff arrived.
TASK = "renamed the payment client to BillingGateway and migrated its callers"
NEXT_STEP = "fix the failing retry test in tests/test_billing.py, which asserts 3 attempts"

# A distractor: a real recorded decision naming a class that was considered and
# rejected. An agent pattern-matching on "the class name near the rename" can
# pick this instead of the right answer, so recall alone will not score.
DISTRACTOR = "considered naming it LedgerClient before settling on the current name"
# A second decision, so the retry-limit inference has something to compute from.
RETRY_POLICY = "set the retry ceiling to 5 attempts across all payment clients"

# Probe kinds:
#   recall       the answer is a literal span of the recorded context
#   distractor   a plausible wrong answer appears verbatim in the context
#   inference    the answer is not written anywhere and must be worked out
#   unanswerable the context does not contain it; guessing scores as a loss
PROBES = [
    (
        "class", "distractor",
        ["billinggateway"], ["ledgerclient"],
        "What class is the payment client called now?",
    ),
    (
        "file", "recall",
        ["tests/test_billing.py", "test_billing"], [],
        "Which test file is failing?",
    ),
    (
        "count", "recall",
        ["3", "three"], [],
        "How many retry attempts does that test assert?",
    ),
    (
        "headroom", "inference",
        ["2", "two"], [],
        "The retry ceiling is a recorded decision and the failing test asserts a "
        "smaller number. How many attempts short of the ceiling is the test? "
        "Answer with the number only.",
    ),
    (
        "owner", "unanswerable",
        ["unknown", "not recorded", "does not say", "no record", "not in the context"],
        ["alice", "bob", "team", "me", "you"],
        "Which engineer is assigned to fix the failing test? If the context does "
        "not record it, reply exactly: UNKNOWN",
    ),
]

QUESTION = (
    "Answer these five questions using only the project context you were given. "
    "One short line each, numbered to match. If the context does not contain an "
    "answer, say UNKNOWN for that number rather than guessing.\n"
    + "\n".join(f"{index + 1}. {probe[4]}" for index, probe in enumerate(PROBES))
)

# The categories LongMemEval separates out. Recall alone flatters a memory
# system: the harder questions are whether it drops superseded facts, keeps
# order straight, reaches back past the most recent session, and admits when it
# does not know.
FIDELITY_SUITES = {
    "knowledge_update": {
        "setup": [
            ("handoff", {"task": "retry limit set to 3 attempts", "next_step": "confirm with QA"}),
            ("handoff", {"task": "QA rejected 3; retry limit raised to 7 attempts", "next_step": "update the test"}),
        ],
        "question": "What is the current retry limit? Answer with the number only.",
        "expect": ["7", "seven"],
        "reject": ["3 attempts", "three attempts"],
    },
    "temporal": {
        "setup": [
            ("handoff", {"task": "wrote the migration script", "next_step": "run it on staging"}),
            ("handoff", {"task": "ran the migration on staging", "next_step": "run it on production"}),
        ],
        "question": "Which happened first, writing the migration or running it on staging?",
        "expect": ["wrote", "writing", "written"],
        "reject": [],
    },
    "multi_session": {
        "setup": [
            ("handoff", {"task": "picked PostgreSQL over MySQL for the audit log", "next_step": "write the schema"}),
        ]
        + [("agent_exit", {"summary": f"session {index} touched module_{index}.py", "returncode": 0}) for index in range(25)]
        + [("handoff", {"task": "wrote the audit log schema", "next_step": "add indexes"})],
        "question": "Which database was chosen for the audit log, and what was it chosen over?",
        "expect": ["postgres"],
        "reject": [],
    },
    "abstention": {
        "setup": [("handoff", {"task": "added the retry helper", "next_step": "write its test"})],
        "question": (
            "What is the production database password for this project? "
            "If the context does not contain it, reply exactly: UNKNOWN"
        ),
        "expect": ["unknown", "not in", "no ", "does not"],
        "reject": [],
    },
}


def force_remove(root: Path) -> None:
    """Delete a tree including Git's read-only object files, which Windows refuses."""
    import os
    import stat

    def clear_readonly(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    if root.exists():
        shutil.rmtree(root, onerror=clear_readonly)


def build_project(root: Path) -> MemoryStore:
    force_remove(root)
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=False, capture_output=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=root, check=False, capture_output=True)
    store = MemoryStore(root)
    store.initialize(100_000, 0.80)
    # The distractor and the retry ceiling are recorded first, then buried under
    # routine noise. The distractor gives the wrong answer somewhere to be found;
    # the ceiling is what the inference probe is computed against.
    store.event("handoff", {"task": DISTRACTOR, "next_step": "confirm the name with the team"})
    store.event("handoff", {"task": RETRY_POLICY, "next_step": "apply it to the billing client"})
    # Background noise, so the handoff is not the only thing in memory.
    for index in range(40):
        store.event("agent_exit", {"summary": f"session {index} touched module_{index}.py", "returncode": 0})
    store.event("handoff", {"task": TASK, "next_step": NEXT_STEP})
    store.write_handoff(TASK, NEXT_STEP)
    return store


ANSWER_LINE = re.compile(r"^\s*\**\s*(\d{1,2})\s*[.)\]:-]\s*(.*)$")


def split_answers(reply: str) -> dict[int, str]:
    """Group a numbered reply into answers by question number.

    Continuation lines belong to the answer above them, so a wrapped or
    multi-line answer is not silently dropped.
    """
    answers: dict[int, list[str]] = {}
    current: int | None = None
    for line in reply.splitlines():
        match = ANSWER_LINE.match(line)
        if match:
            current = int(match.group(1))
            answers.setdefault(current, []).append(match.group(2))
        elif current is not None and line.strip():
            answers[current].append(line.strip())
    return {number: " ".join(parts).strip().lower() for number, parts in answers.items()}


def score(reply: str) -> tuple[int, list[str]]:
    """Score each probe against the answer numbered for it, and nothing else.

    Searching the whole reply for every accepted term lets one answer satisfy a
    different question: "the retry test asserts 3 attempts" contains "retry", so
    it used to satisfy a separate question about the next action even when that
    question went unanswered. Scoring per numbered answer is what makes a
    reported percentage mean what it appears to mean.

    A probe is only credited when its answer contains an accepted term and no
    rejected one. That is what stops a distractor question being passed by
    naming both candidates, and stops an unanswerable question being passed by
    hedging with a guess attached.
    """
    answers = split_answers(reply)
    missed = []
    hits = 0
    for index, (name, _kind, accepted, rejected, _question) in enumerate(PROBES, start=1):
        answer = answers.get(index, "")
        hit = bool(answer) and any(term in answer for term in accepted)
        spoiled = any(term in answer for term in rejected)
        if hit and not spoiled:
            hits += 1
        else:
            missed.append(name)
    return hits, missed


def score_by_kind(reply: str) -> dict[str, bool]:
    """Per-probe outcome, so results can be broken down by probe kind."""
    answers = split_answers(reply)
    outcome = {}
    for index, (name, _kind, accepted, rejected, _question) in enumerate(PROBES, start=1):
        answer = answers.get(index, "")
        hit = bool(answer) and any(term in answer for term in accepted)
        outcome[name] = hit and not any(term in answer for term in rejected)
    return outcome


def bootstrap_interval(values: list[float], confidence: float = 0.95,
                       resamples: int = 5000) -> tuple[float, float]:
    """A percentile bootstrap interval, so no distribution is assumed.

    Accuracy over a handful of trials is not normal, and quoting a mean without
    an interval is what made the earlier numbers unquotable. Resampling needs
    nothing but the standard library.
    """
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (values[0], values[0])
    means = []
    for _ in range(resamples):
        sample = random.choices(values, k=len(values))
        means.append(sum(sample) / len(sample))
    means.sort()
    low = means[int((1 - confidence) / 2 * resamples)]
    high = means[min(resamples - 1, int((1 + confidence) / 2 * resamples))]
    return (round(low, 2), round(high, 2))


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """A Wilson score interval on the proportion, as a percentage.

    The percentile bootstrap this replaced could only resample the values it was
    given, so a cell where all 30 trials scored identically produced [100, 100]:
    an interval asserting no uncertainty at all, from 30 observations. That is
    false, and it is worse than no interval, because it invites the reader to
    believe the sample settled the question.

    Wilson keeps a boundary away from the edge. Thirty trials of five probes,
    all correct, gives a lower bound near 97.5% rather than 100%, which is what
    150 successes actually support.
    """
    if total <= 0:
        return (0.0, 0.0)
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    spread = z * ((proportion * (1 - proportion) / total + z * z / (4 * total * total)) ** 0.5) / denominator
    low = max(0.0, centre - spread) * 100
    high = min(1.0, centre + spread) * 100
    return (round(low, 2), round(high, 2))


def context_sizes(store: MemoryStore) -> dict:
    raw = "\n".join(
        json.dumps(item, ensure_ascii=True) for item in store.recent_events(200)
    )
    sizes = {"raw_history": estimate_tokens(raw)}
    for mode in ("compact", "normal", "deep"):
        sizes[mode] = estimate_tokens(store.resume_context(mode))
    # Characters as well, because they are exact. estimate_tokens divides by
    # four, so the token figures are an estimate and should be published as one.
    # The ratio between two of them is unaffected, since the same divisor is on
    # both sides.
    sizes["characters"] = {
        "raw_history": len(raw),
        **{mode: len(store.resume_context(mode)) for mode in ("compact", "normal", "deep")},
    }
    return sizes


def trial(store: MemoryStore, agent: str, with_context: bool) -> dict:
    started = time.time()
    try:
        if with_context:
            result = ask(store, agent, QUESTION, sender="benchmark", timeout=240)
            reply, tokens = result["reply"], result["prompt_tokens"]
        else:
            from continuum.agents import launch_args, resolve
            from continuum.cli import agent_command

            spec = resolve(store, agent)
            passthrough = [str(item) for item in spec.get("oneshot_args", [])]
            control = (
                "Answer from what you already know; if you do not know, say UNKNOWN. "
                "Do not read files. " + QUESTION
            ).replace("\n", " ")
            completed = subprocess.run(
                agent_command(spec, launch_args(spec, passthrough, control)),
                cwd=str(store.project), input="", capture_output=True, text=True,
                errors="replace", timeout=240,
            )
            reply, tokens = completed.stdout.strip(), estimate_tokens(control)
    except (DelegationError, subprocess.TimeoutExpired, OSError) as error:
        return {"ok": False, "error": str(error)[:160], "seconds": round(time.time() - started, 1)}
    hits, missed = score(reply)
    return {
        "ok": True,
        "seconds": round(time.time() - started, 1),
        "prompt_tokens": tokens,
        "reply_tokens": estimate_tokens(reply),
        "score": hits,
        "missed": missed,
        "by_probe": score_by_kind(reply),
    }


def scored_delegation(entry: dict) -> bool:
    """Whether a delegation entry came from a harness that read the reply.

    Entries written before the reply was scored carry `accuracy_pct: 0.0` from
    a hardcoded zero, and `--resume` would keep them and the report would print
    them as 0% delivered. The scored ones record the `pong` probe, so its
    presence is what distinguishes them. Anything else is treated as unrecorded
    and re-run.
    """
    return bool(entry.get("completed")) and "pong" in (entry.get("per_probe_pct") or {})


def summarize(rows: list[dict], max_score: int | None = None) -> dict:
    good = [row for row in rows if row.get("ok")]
    if not good:
        return {"runs": len(rows), "completed": 0}
    scores = [row["score"] for row in good]
    seconds = [row["seconds"] for row in good]
    # The fidelity arms ask all five probes. Other arms ask fewer, and dividing
    # their scores by five understates them: the delegation arm asks one thing
    # and was published as 0% accurate because of it.
    denominator = max_score or len(PROBES)
    per_trial_pct = [100.0 * value / denominator for value in scores]
    # On the probe answers rather than on the trial means. Accuracy here is
    # exactly (probes passed) / (probes asked), so a proportion interval is the
    # right instrument, and unlike a bootstrap it does not collapse to a point
    # when every trial happens to score the same.
    low, high = wilson_interval(sum(scores), denominator * len(good))
    # Per probe kind, so a headline number cannot hide a category that always
    # fails. Each probe contributes one pass or fail per completed trial.
    by_kind: dict[str, list[int]] = {}
    for row in good:
        for name, passed in (row.get("by_probe") or {}).items():
            by_kind.setdefault(name, []).append(1 if passed else 0)
    return {
        "runs": len(rows),
        "completed": len(good),
        "completion_pct": round(100 * len(good) / len(rows), 1) if rows else 0.0,
        "score_mean": round(statistics.mean(scores), 2),
        "score_sd": round(statistics.pstdev(scores), 2) if len(scores) > 1 else 0.0,
        "accuracy_pct": round(statistics.mean(per_trial_pct), 1),
        "accuracy_ci95": [low, high],
        "seconds_mean": round(statistics.mean(seconds), 1),
        "seconds_median": round(statistics.median(seconds), 1),
        "seconds_max": round(max(seconds), 1),
        # A machine that suspends mid-trial charges the whole suspension to that
        # trial. It happened: a laptop slept for ten hours and moved a cell's
        # mean from about forty seconds to 1217.5. Accuracy is unharmed, since a
        # correct answer stays correct, but the timing is not a measurement of
        # anything. Flag it rather than drop it, so the decision is visible.
        "timing_suspect": max(seconds) > 20 * max(statistics.median(seconds), 0.1),
        "prompt_tokens": good[0].get("prompt_tokens"),
        "reply_tokens_mean": round(statistics.mean(r["reply_tokens"] for r in good), 1),
        "per_probe_pct": {
            name: round(100 * sum(values) / len(values), 1) for name, values in sorted(by_kind.items())
        },
        "missed": sorted({name for row in good for name in row["missed"]}),
        "errors": [row.get("error") for row in rows if not row.get("ok")][:3],
    }


# The injected handoff and the on-disk handoff are made to disagree. An agent
# that answers "Ledger" read the files itself and Continuum's injection did
# nothing; an agent that answers "BillingGateway" used what was injected. Without
# this arm a passing score proves only that the agent can open a file.
CONFLICT_DISK_TASK = "renamed the payment client to LedgerClient"
CONFLICT_QUESTION = "What was the payment client renamed to? One word."


INJECTED_CONFLICT_TEXT = (
    "# Current\n\n"
    "Task: renamed the payment client to BillingGateway\n"
    "Changes: No recorded file changes.\n"
    "Blocker: None recorded.\n"
    "Next: confirm the rename\n"
)


def conflict_trial(store: MemoryStore, agent: str) -> dict:
    """Answer sourced from injected context, from disk, or from neither.

    Continuum builds the injected prompt by reading the same files the agent can
    open, so the two have to be forced apart: resume_context is overridden to
    return BillingGateway while the files on disk say LedgerClient.
    """
    store.resume_context = lambda _mode="compact": INJECTED_CONFLICT_TEXT  # type: ignore[method-assign]
    started = time.time()
    try:
        reply = ask(store, agent, CONFLICT_QUESTION, sender="benchmark", timeout=240)["reply"]
    except (DelegationError, OSError) as error:
        return {"ok": False, "error": str(error)[:120]}
    lowered = reply.lower()
    if "billinggateway" in lowered:
        source = "injected_context"
    elif "ledger" in lowered:
        source = "read_from_disk"
    else:
        source = "neither"
    return {"ok": True, "source": source, "seconds": round(time.time() - started, 1),
            "reply": reply.replace("\n", " ")[:80]}


def with_isolated_project(run):
    """Run `run(store)` against an empty project far away from this benchmark.

    The control arm lets the agent read files, so it must not be able to reach
    the other generated projects by walking up or sideways from its working
    directory. A system temporary directory is unrelated to the benchmark tree;
    a subdirectory of it is not.
    """
    with tempfile.TemporaryDirectory(prefix="continuum_control_") as temporary:
        root = Path(temporary) / "repo"
        root.mkdir()
        # Codex refuses to run outside a Git repository, and a refusal recorded
        # as a score of zero is what produced the bogus control result in
        # docs/benchmarks.md.
        subprocess.run(["git", "init", "-q"], cwd=root, check=False, capture_output=True)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"],
                       cwd=root, check=False, capture_output=True)
        blank = MemoryStore(root)
        blank.initialize(100_000, 0.80)
        return run(blank)


def bare_agent_trial(store: MemoryStore, agent: str, question: str) -> dict:
    """Run the agent with no injected context, in a project that still has files.

    This is the arm that decides whether Continuum is doing the work. The agent
    is free to open `.continuum/` itself; if it scores as well here as with
    injection, the injection is not what produced the answer.
    """
    from continuum.agents import launch_args, resolve
    from continuum.cli import agent_command

    spec = resolve(store, agent)
    passthrough = [str(item) for item in spec.get("oneshot_args", [])]
    started = time.time()
    try:
        completed = subprocess.run(
            agent_command(spec, launch_args(spec, passthrough, question.replace("\n", " "))),
            cwd=str(store.project), input="", capture_output=True, text=True,
            errors="replace", timeout=240,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "error": str(error)[:120]}
    reply = completed.stdout.strip()
    if completed.returncode != 0:
        # An agent that refused to start has not answered badly, it has not
        # answered at all. Scoring the refusal as zero is what produced the
        # bogus Codex control result recorded in docs/benchmarks.md, so a
        # non-zero exit is reported as an incomplete trial instead.
        detail = (completed.stderr or reply or "no output").strip().splitlines()
        return {"ok": False, "error": f"exit {completed.returncode}: {detail[-1][:110] if detail else ''}"}
    if not reply:
        return {"ok": False, "error": "the agent produced no output"}
    hits, missed = score(reply)
    return {"ok": True, "seconds": round(time.time() - started, 1), "score": hits,
            "missed": missed, "prompt_tokens": estimate_tokens(question),
            "reply_tokens": estimate_tokens(reply), "by_probe": score_by_kind(reply)}


def run_suite(root: Path, agent: str, name: str, suite: dict, trials: int) -> dict:
    """Score one LongMemEval-style category against a purpose-built project."""
    store = MemoryStore(root)
    force_remove(root)
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=False, capture_output=True)
    store.initialize(100_000, 0.80)
    for kind, payload in suite["setup"]:
        store.event(kind, payload)
        if kind == "handoff":
            store.write_handoff(payload["task"], payload["next_step"])

    passes, seconds, replies = 0, [], []
    for _ in range(trials):
        started = time.time()
        try:
            reply = ask(store, agent, suite["question"], sender="benchmark", timeout=240)["reply"]
        except (DelegationError, OSError) as error:
            replies.append(f"ERROR {error}"[:120])
            continue
        seconds.append(round(time.time() - started, 1))
        lowered = reply.lower()
        hit = any(term in lowered for term in suite["expect"])
        stale = any(term in lowered for term in suite.get("reject", []))
        passes += 1 if (hit and not stale) else 0
        replies.append(reply.replace("\n", " ")[:90])
    completed = len(seconds)
    return {
        "category": name,
        "agent": agent,
        "trials": trials,
        "completed": completed,
        "passed": passes,
        "pass_pct": round(100 * passes / trials, 1),
        "seconds_mean": round(statistics.mean(seconds), 1) if seconds else None,
        "samples": replies[:2],
    }


def render_chart(report: dict, path: Path) -> None:
    """Write a grouped bar chart of accuracy by arm, with confidence intervals.

    SVG is emitted directly rather than through a plotting library: it keeps the
    repository's dependencies at two, renders natively on GitHub, and diffs as
    text so a change to a published chart is reviewable.
    """
    arms = ["injected", "files_only", "no_memory"]
    labels = {"injected": "Continuum injects", "files_only": "reads files itself", "no_memory": "no memory"}
    agents = sorted({key.split("/")[0] for key in report.get("fidelity", {})})
    if not agents:
        return
    colours = {"injected": "#2f6f4e", "files_only": "#7d8ca3", "no_memory": "#b4544a"}

    left, top, width, height = 150, 40, 620, 60 * len(agents) * len(arms) + 40
    bar_h, gap = 26, 8
    rows = []
    y = top
    for agent in agents:
        rows.append(("agent", agent, y))
        y += 24
        for arm in arms:
            summary = report["fidelity"].get(f"{agent}/{arm}") or {}
            if summary.get("completed"):
                rows.append(("bar", (arm, summary), y))
            else:
                rows.append(("missing", arm, y))
            y += bar_h + gap
        y += 14
    total = y + 40

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{left + width + 40}" height="{total}" '
        f'viewBox="0 0 {left + width + 40} {total}" font-family="system-ui,sans-serif" font-size="13">',
        f'<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="24" font-size="15" font-weight="600">Answers correct by arm, '
        f'{report.get("trials", "?")} trials each, 95% confidence interval</text>',
    ]
    for value in (0, 25, 50, 75, 100):
        x = left + width * value / 100
        parts.append(f'<line x1="{x:.0f}" y1="{top}" x2="{x:.0f}" y2="{total - 34}" stroke="#e6e6e6"/>')
        parts.append(f'<text x="{x:.0f}" y="{total - 18}" text-anchor="middle" fill="#666">{value}%</text>')

    for kind, payload, y in rows:
        if kind == "agent":
            parts.append(f'<text x="8" y="{y + 14}" font-weight="600">{payload}</text>')
        elif kind == "missing":
            parts.append(f'<text x="{left - 8}" y="{y + 18}" text-anchor="end" fill="#666">{labels[payload]}</text>')
            parts.append(f'<text x="{left + 6}" y="{y + 18}" fill="#999">not measured</text>')
        else:
            arm, summary = payload
            pct = float(summary.get("accuracy_pct") or 0.0)
            low, high = (summary.get("accuracy_ci95") or [pct, pct])[:2]
            bar = width * pct / 100
            parts.append(f'<text x="{left - 8}" y="{y + 18}" text-anchor="end" fill="#444">{labels[arm]}</text>')
            parts.append(
                f'<rect x="{left}" y="{y}" width="{bar:.1f}" height="{bar_h}" fill="{colours[arm]}" rx="3"/>'
            )
            x_low, x_high = left + width * float(low) / 100, left + width * float(high) / 100
            mid = y + bar_h / 2
            parts.append(
                f'<line x1="{x_low:.1f}" y1="{mid}" x2="{x_high:.1f}" y2="{mid}" stroke="#22303c" stroke-width="2"/>'
                f'<line x1="{x_low:.1f}" y1="{y + 5}" x2="{x_low:.1f}" y2="{y + bar_h - 5}" stroke="#22303c" stroke-width="2"/>'
                f'<line x1="{x_high:.1f}" y1="{y + 5}" x2="{x_high:.1f}" y2="{y + bar_h - 5}" stroke="#22303c" stroke-width="2"/>'
            )
            # A 100% bar reaches the right edge, so a label placed after it is
            # clipped at the viewport and the interval disappears from exactly
            # the results this chart exists to show. Past three quarters the
            # label moves inside the bar and right-aligns instead.
            label = f'{pct:.0f}% ({low:.0f} to {high:.0f})'
            if pct > 75:
                parts.append(
                    f'<text x="{left + bar - 8:.1f}" y="{y + 18}" text-anchor="end" '
                    f'fill="#ffffff">{label}</text>'
                )
            else:
                parts.append(
                    f'<text x="{left + bar + 8:.1f}" y="{y + 18}" fill="#222">{pct:.0f}% '
                    f'<tspan fill="#666">({low:.0f} to {high:.0f})</tspan></text>'
                )
    parts.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


# Bumped whenever a change makes older cells unsafe to keep: a different probe
# set, a different scorer, or a different set of arms.
SCHEMA = 2


def load_partial(path: Path, trials: int) -> dict:
    """Reload a previous run so an interrupted one can be continued.

    A full run is hours of real agent calls. Losing it to one quota failure near
    the end would make the measurement impractical to repeat, which is the same
    as not being reproducible.

    Cells are only kept when they were measured the same way. Resuming across a
    scorer change, a probe change or a different trial count would republish old
    numbers under the new run's headline, which is how the withdrawn three-trial
    scores could have reappeared as thirty-trial results.
    """
    if not path.exists():
        return {}
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(previous, dict):
        return {}

    reasons = []
    if int(previous.get("schema") or 0) != SCHEMA:
        reasons.append(f"schema {previous.get('schema')!r}, expected {SCHEMA}")
    if int(previous.get("trials") or 0) != trials:
        reasons.append(f"{previous.get('trials')} trials, this run wants {trials}")
    if previous.get("probe_kinds") != {probe[0]: probe[1] for probe in PROBES}:
        reasons.append("a different probe set")
    if reasons:
        print(f"ignoring {path}: it was produced with " + "; ".join(reasons), flush=True)
        return {}
    return previous


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=30)
    parser.add_argument("--agents", default="claude,codex")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "results.json"))
    parser.add_argument("--resume", action="store_true",
                        help="Keep cells already present in the output file and only run the rest.")
    parser.add_argument("--chart", default=None,
                        help="Write an SVG chart of accuracy by arm to this path.")
    args = parser.parse_args()
    agents = [name.strip() for name in args.agents.split(",") if name.strip()]
    out = Path(args.out)

    out.parent.mkdir(parents=True, exist_ok=True)
    previous = load_partial(out, args.trials) if args.resume else {}
    report: dict = {"schema": SCHEMA, "probes": len(PROBES), "trials": args.trials,
                    "probe_kinds": {probe[0]: probe[1] for probe in PROBES}}
    if previous:
        report.update({key: value for key, value in previous.items()
                       if key not in ("schema", "probes", "trials", "probe_kinds")})
        print(f"resuming from {out}", flush=True)

    def save() -> None:
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def done(section: str, key: str) -> bool:
        return bool(args.resume and report.get(section, {}).get(key, {}).get("completed"))

    def cell(section: str, key: str, run) -> None:
        """Run one measurement cell, unless a resumed file already has it."""
        if done(section, key):
            print(f"  {key}: already recorded, skipping", flush=True)
            return
        report.setdefault(section, {})[key] = summarize(run())
        summary = report[section][key]
        print(
            f"  {key}: {summary.get('accuracy_pct')}% "
            f"CI{summary.get('accuracy_ci95')} "
            f"completed {summary.get('completed')}/{summary.get('runs')} "
            f"{summary.get('seconds_mean')}s",
            flush=True,
        )
        save()

    root = Path(__file__).resolve().parent / "project"
    store = build_project(root)

    report["context_tokens"] = context_sizes(store)
    print("context tokens:", report["context_tokens"], flush=True)
    save()

    report.setdefault("fidelity", {})
    for agent in agents:
        # Arm A: Continuum injects the context.
        cell("fidelity", f"{agent}/injected",
             lambda: [trial(store, agent, True) for _ in range(args.trials)])

        # Arm B: no injection, but the project's .continuum files are right
        # there for the agent to open. This is the arm that decides whether the
        # injection is doing the work.
        cell("fidelity", f"{agent}/files_only",
             lambda: [bare_agent_trial(store, agent, QUESTION) for _ in range(args.trials)])

        # Arm C: no injection, no project memory at all. The agent is allowed to
        # read files here, so this has to live somewhere unrelated to the
        # benchmark: an earlier version put it beside the other generated
        # projects and Codex found the answers by searching sibling directories,
        # scoring 5/5 in a project that contained nothing.
        cell("fidelity", f"{agent}/no_memory",
             lambda: with_isolated_project(lambda blank: [
                 bare_agent_trial(blank, agent, QUESTION) for _ in range(args.trials)
             ]))

    # Arm D: injected context and on-disk files disagree. Which one is answered
    # from tells us whether injection or file access produced the earlier score.
    # Arm D and everything after it checkpoint the same way fidelity does. Each
    # of these phases is another thirty real agent calls per agent, so losing a
    # completed one to an interruption in the next is the same waste.
    report.setdefault("conflict", {})
    conflict_root = Path(__file__).resolve().parent / "conflict"
    for agent in agents:
        if args.resume and report["conflict"].get(agent, {}).get("trials"):
            print(f"  conflict/{agent}: already recorded, skipping", flush=True)
            continue
        conflict_store = build_project(conflict_root)
        conflict_store.write_handoff(CONFLICT_DISK_TASK, "confirm the rename")
        conflict_store.event("handoff", {"task": TASK, "next_step": NEXT_STEP})
        rows = [conflict_trial(conflict_store, agent) for _ in range(args.trials)]
        sources = [row.get("source") for row in rows if row.get("ok")]
        report["conflict"][agent] = {
            "trials": args.trials,
            "completed": len(sources),
            "answered_from_injected": sources.count("injected_context"),
            "answered_from_disk": sources.count("read_from_disk"),
            "neither": sources.count("neither"),
            "samples": [row.get("reply") for row in rows if row.get("ok")][:2],
        }
        print(f"  conflict/{agent}: {report['conflict'][agent]}", flush=True)
        save()

    report.setdefault("categories", [])
    recorded = {(row.get("agent"), row.get("category")) for row in report["categories"]}
    suite_root = Path(__file__).resolve().parent / "suite"
    for agent in agents:
        for name, suite in FIDELITY_SUITES.items():
            if args.resume and (agent, name) in recorded:
                print(f"  {agent}/{name}: already recorded, skipping", flush=True)
                continue
            row = run_suite(suite_root, agent, name, suite, args.trials)
            # Replace rather than append, so a partial rerun cannot leave two
            # rows for the same cell.
            report["categories"] = [
                item for item in report["categories"]
                if (item.get("agent"), item.get("category")) != (agent, name)
            ] + [row]
            print(f"  {agent}/{name}: {row['passed']}/{row['trials']} {row['samples'][:1]}", flush=True)
            save()

    report.setdefault("delegation", {})
    for agent in agents:
        if args.resume and scored_delegation(report["delegation"].get(agent, {})):
            print(f"  delegation/{agent}: already recorded, skipping", flush=True)
            continue
        rows = []
        for _ in range(args.trials):
            started = time.time()
            try:
                result = ask(store, agent, "Reply with exactly: PONG", sender="benchmark", timeout=180)
                # Whether the message actually arrived and came back, rather
                # than whether a process exited cleanly. Scoring every trial 0
                # made this cell read as 0% accurate delegation, which is not
                # something it ever measured.
                delivered = "pong" in str(result.get("reply", "")).lower()
                rows.append({"ok": True, "seconds": round(time.time() - started, 1),
                             "prompt_tokens": result["prompt_tokens"],
                             "score": 1 if delivered else 0,
                             "reply_tokens": result["reply_tokens"],
                             "missed": [] if delivered else ["pong"],
                             "by_probe": {"pong": delivered}})
            except (DelegationError, OSError) as error:
                rows.append({"ok": False, "error": str(error)[:120]})
        report["delegation"][agent] = summarize(rows, max_score=1)
        print(f"  delegation/{agent}: {report['delegation'][agent].get('seconds_mean')}s "
              f"delivered {report['delegation'][agent].get('accuracy_pct')}% "
              f"completed {report['delegation'][agent].get('completed')}/{args.trials}", flush=True)
        save()

    save()
    print("\nwrote", out)
    if args.chart:
        render_chart(report, Path(args.chart))
        print("wrote", args.chart)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
