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
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

from continuum.core import MemoryStore, estimate_tokens  # noqa: E402
from continuum.delegation import DelegationError, ask  # noqa: E402

# The recorded project state. Every probe below is answerable from this and
# from nothing else, so a correct answer proves the handoff arrived.
TASK = "renamed the payment client to BillingGateway and migrated its callers"
NEXT_STEP = "fix the failing retry test in tests/test_billing.py, which asserts 3 attempts"

PROBES = [
    ("class", ["billinggateway"], "What class was the payment client renamed to?"),
    ("file", ["tests/test_billing.py", "test_billing"], "Which test file is failing?"),
    ("count", ["3", "three"], "How many retry attempts does that test assert?"),
    ("verb", ["migrat", "updat", "callers"], "What was done to the callers?"),
    ("phase", ["fix", "retry"], "In two words, what is the next action?"),
]

QUESTION = (
    "Answer these five questions from the project context you were given. "
    "One short line each, numbered. Do not ask for files.\n"
    + "\n".join(f"{index + 1}. {probe[2]}" for index, probe in enumerate(PROBES))
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
    # Background noise, so the handoff is not the only thing in memory.
    for index in range(40):
        store.event("agent_exit", {"summary": f"session {index} touched module_{index}.py", "returncode": 0})
    store.event("handoff", {"task": TASK, "next_step": NEXT_STEP})
    store.write_handoff(TASK, NEXT_STEP)
    return store


def score(reply: str) -> tuple[int, list[str]]:
    lowered = reply.lower()
    missed = []
    hits = 0
    for name, accepted, _ in PROBES:
        if any(term in lowered for term in accepted):
            hits += 1
        else:
            missed.append(name)
    return hits, missed


def context_sizes(store: MemoryStore) -> dict:
    raw = "\n".join(
        json.dumps(item, ensure_ascii=True) for item in store.recent_events(200)
    )
    sizes = {"raw_history": estimate_tokens(raw)}
    for mode in ("compact", "normal", "deep"):
        sizes[mode] = estimate_tokens(store.resume_context(mode))
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
    }


def summarize(rows: list[dict]) -> dict:
    good = [row for row in rows if row.get("ok")]
    if not good:
        return {"runs": len(rows), "completed": 0}
    scores = [row["score"] for row in good]
    seconds = [row["seconds"] for row in good]
    return {
        "runs": len(rows),
        "completed": len(good),
        "score_mean": round(statistics.mean(scores), 2),
        "score_sd": round(statistics.pstdev(scores), 2) if len(scores) > 1 else 0.0,
        "accuracy_pct": round(100 * sum(scores) / (len(scores) * len(PROBES)), 1),
        "seconds_mean": round(statistics.mean(seconds), 1),
        "prompt_tokens": good[0].get("prompt_tokens"),
        "reply_tokens_mean": round(statistics.mean(r["reply_tokens"] for r in good), 1),
        "missed": sorted({name for row in good for name in row["missed"]}),
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
    hits, missed = score(reply)
    return {"ok": True, "seconds": round(time.time() - started, 1), "score": hits,
            "missed": missed, "prompt_tokens": estimate_tokens(question),
            "reply_tokens": estimate_tokens(reply)}


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--agents", default="claude,codex")
    args = parser.parse_args()
    agents = [name.strip() for name in args.agents.split(",") if name.strip()]

    root = Path(__file__).resolve().parent / "project"
    store = build_project(root)
    report: dict = {"probes": len(PROBES), "trials": args.trials}

    report["context_tokens"] = context_sizes(store)
    print("context tokens:", report["context_tokens"], flush=True)

    report["fidelity"] = {}
    for agent in agents:
        # Arm A: Continuum injects the context.
        rows = [trial(store, agent, True) for _ in range(args.trials)]
        report["fidelity"][f"{agent}/injected"] = summarize(rows)
        print(f"  {agent}/injected: {report['fidelity'][f'{agent}/injected']}", flush=True)

        # Arm B: no injection, but the project's .continuum files are right
        # there for the agent to open. This is the arm that decides whether the
        # injection is doing the work.
        rows = [bare_agent_trial(store, agent, QUESTION) for _ in range(args.trials)]
        report["fidelity"][f"{agent}/files_only"] = summarize(rows)
        print(f"  {agent}/files_only: {report['fidelity'][f'{agent}/files_only']}", flush=True)

        # Arm C: no injection, no project memory at all.
        empty = Path(__file__).resolve().parent / "empty"
        force_remove(empty)
        empty.mkdir(parents=True, exist_ok=True)
        # Codex refuses to run outside a Git repository, so without this the
        # control arm records a refusal as a score of zero and looks like a
        # floor measurement when nothing was measured at all.
        subprocess.run(["git", "init", "-q"], cwd=empty, check=False, capture_output=True)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"],
                       cwd=empty, check=False, capture_output=True)
        blank = MemoryStore(empty)
        blank.initialize(100_000, 0.80)
        rows = [bare_agent_trial(blank, agent, QUESTION) for _ in range(args.trials)]
        report["fidelity"][f"{agent}/no_memory"] = summarize(rows)
        print(f"  {agent}/no_memory: {report['fidelity'][f'{agent}/no_memory']}", flush=True)

    # Arm D: injected context and on-disk files disagree. Which one is answered
    # from tells us whether injection or file access produced the earlier score.
    report["conflict"] = {}
    conflict_root = Path(__file__).resolve().parent / "conflict"
    for agent in agents:
        conflict_store = build_project(conflict_root)
        conflict_store.write_handoff(CONFLICT_DISK_TASK, "confirm the rename")
        conflict_store.event("handoff", {"task": TASK, "next_step": NEXT_STEP})
        rows = [conflict_trial(conflict_store, agent) for _ in range(args.trials)]
        sources = [row.get("source") for row in rows if row.get("ok")]
        report["conflict"][agent] = {
            "trials": args.trials,
            "answered_from_injected": sources.count("injected_context"),
            "answered_from_disk": sources.count("read_from_disk"),
            "neither": sources.count("neither"),
            "samples": [row.get("reply") for row in rows if row.get("ok")][:2],
        }
        print(f"  conflict/{agent}: {report['conflict'][agent]}", flush=True)

    report["categories"] = []
    suite_root = Path(__file__).resolve().parent / "suite"
    for agent in agents:
        for name, suite in FIDELITY_SUITES.items():
            row = run_suite(suite_root, agent, name, suite, args.trials)
            report["categories"].append(row)
            print(f"  {agent}/{name}: {row['passed']}/{row['trials']} {row['samples'][:1]}", flush=True)

    report["delegation"] = {}
    for agent in agents:
        rows = []
        for _ in range(args.trials):
            started = time.time()
            try:
                result = ask(store, agent, "Reply with exactly: PONG", sender="benchmark", timeout=180)
                rows.append({"ok": True, "seconds": round(time.time() - started, 1),
                             "prompt_tokens": result["prompt_tokens"],
                             "score": 0, "reply_tokens": result["reply_tokens"], "missed": []})
            except (DelegationError, OSError) as error:
                rows.append({"ok": False, "error": str(error)[:120]})
        report["delegation"][agent] = summarize(rows)
        print(f"  delegation/{agent}: {report['delegation'][agent]}", flush=True)

    out = Path(__file__).resolve().parent / "results.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\nwrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
