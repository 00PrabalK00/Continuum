"""Turn a benchmark run into the published tables.

Every figure in docs/benchmarks.md and the README results section is generated
from results.json rather than typed. Three of the four instrument faults this
benchmark has had were only visible by comparing a published number against how
it was produced, and hand-copied tables are exactly where that comparison stops
being possible.

Usage:
    python benchmarks/report.py --results benchmarks/results.json --write
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ARMS = [
    ("injected", "Continuum injects the context"),
    ("files_only", "No injection, the agent reads `.continuum/` itself"),
    ("no_memory", "No project memory at all"),
]
PROBE_BLURB = {
    "recall": "the answer is written in the context",
    "distractor": "a plausible wrong answer is also in the context",
    "inference": "the answer is not written anywhere and must be worked out",
    "unanswerable": "the context does not contain it, so a guess is a loss",
}


def agents_in(report: dict) -> list[str]:
    return sorted({key.split("/")[0] for key in report.get("fidelity", {})})


def cell(report: dict, agent: str, arm: str) -> dict:
    return report.get("fidelity", {}).get(f"{agent}/{arm}") or {}


def accuracy(entry: dict) -> str:
    if not entry.get("completed"):
        return "not measured"
    low, high = (entry.get("accuracy_ci95") or [None, None])[:2]
    text = f"{entry['accuracy_pct']:.0f}%"
    if low is not None:
        text += f" ({low:.0f} to {high:.0f})"
    return text


def completion(entry: dict) -> str:
    if not entry.get("runs"):
        return "-"
    return f"{entry.get('completed', 0)}/{entry['runs']}"


def table(rows: list[list[str]], header: list[str]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def accuracy_table(report: dict) -> str:
    agents = agents_in(report)
    rows = []
    for arm, label in ARMS:
        row = [label]
        for agent in agents:
            row.append(accuracy(cell(report, agent, arm)))
        rows.append(row)
    return table(rows, ["Arm"] + agents)


def timing_table(report: dict) -> str:
    agents = agents_in(report)
    rows = []
    for arm, label in ARMS:
        row = [label]
        for agent in agents:
            entry = cell(report, agent, arm)
            row.append(f"{entry['seconds_mean']:.1f}s" if entry.get("completed") else "-")
        rows.append(row)
    return table(rows, ["Arm"] + agents)


def completion_table(report: dict) -> str:
    agents = agents_in(report)
    rows = []
    for arm, label in ARMS:
        rows.append([label] + [completion(cell(report, agent, arm)) for agent in agents])
    return table(rows, ["Arm"] + agents)


def probe_table(report: dict) -> str:
    kinds = report.get("probe_kinds", {})
    agents = agents_in(report)
    rows = []
    for name, kind in kinds.items():
        row = [f"`{name}`", kind]
        for agent in agents:
            entry = cell(report, agent, "injected")
            value = (entry.get("per_probe_pct") or {}).get(name)
            row.append(f"{value:.0f}%" if value is not None else "-")
        rows.append(row)
    return table(rows, ["Probe", "Kind"] + agents)


def context_table(report: dict) -> str:
    sizes = report.get("context_tokens") or {}
    raw = sizes.get("raw_history")
    rows = []
    for mode in ("deep", "normal", "compact"):
        if mode not in sizes:
            continue
        saved = f"{100 - 100 * sizes[mode] / raw:.0f}% smaller" if raw else ""
        rows.append([mode, f"{sizes[mode]:,}", saved])
    if raw:
        rows.insert(0, ["raw event history", f"{raw:,}", ""])
    return table(rows, ["", "tokens", "against raw"])


def conflict_table(report: dict) -> str:
    rows = []
    for agent, entry in sorted(report.get("conflict", {}).items()):
        total = entry.get("trials", 0)
        rows.append([
            agent,
            f"{entry.get('answered_from_injected', 0)}/{total}",
            f"{entry.get('answered_from_disk', 0)}/{total}",
        ])
    return table(rows, ["", "answered from injected context", "answered from disk"]) if rows else ""


def category_table(report: dict) -> str:
    rows_by_category: dict[str, dict[str, str]] = {}
    for row in report.get("categories", []):
        rows_by_category.setdefault(row["category"], {})[row["agent"]] = (
            f"{row['passed']}/{row['trials']}"
        )
    if not rows_by_category:
        return ""
    agents = agents_in(report)
    rows = [[name] + [values.get(agent, "-") for agent in agents]
            for name, values in sorted(rows_by_category.items())]
    return table(rows, ["Category"] + agents)


def delegation_table(report: dict) -> str:
    rows = []
    for agent, entry in sorted(report.get("delegation", {}).items()):
        if entry.get("completed"):
            rows.append([agent, f"{entry['seconds_mean']:.1f}s", completion(entry)])
    return table(rows, ["", "round trip", "completed"]) if rows else ""


def headline(report: dict) -> str:
    """The two numbers the README leads with, taken from the run itself."""
    agents = agents_in(report)
    best = [cell(report, agent, "injected") for agent in agents]
    floor = [cell(report, agent, "no_memory") for agent in agents]
    measured = [entry for entry in best if entry.get("completed")]
    floors = [entry for entry in floor if entry.get("completed")]
    if not measured or not floors:
        return ""
    top = min(entry["accuracy_pct"] for entry in measured)
    bottom = max(entry["accuracy_pct"] for entry in floors)
    return f"{top:.0f}% correct with Continuum, {bottom:.0f}% without"


def render(report: dict) -> str:
    trials = report.get("trials", "?")
    parts = [
        "# Benchmarks",
        "",
        "Every figure on this page is generated from `benchmarks/results.json` by",
        "`benchmarks/report.py`, which is the only way to keep a published number",
        "and the run that produced it from drifting apart. Three of the four faults",
        "this benchmark has had were found by checking how a number was produced,",
        "not by looking at the number.",
        "",
        "Reproduce with:",
        "",
        "```bash",
        f"python benchmarks/agent_memory_bench.py --trials {trials} --agents claude,codex",
        "python benchmarks/report.py --write",
        "```",
        "",
        "That calls real agent CLIs and spends quota. The context sizes below need",
        "neither, and can be checked in seconds.",
        "",
        "## What is asked",
        "",
        "One project with a known recorded state, and five questions about it. The",
        "probe kinds exist so that copying the context cannot score:",
        "",
        probe_table(report),
        "",
        "Percentages are per probe, on the injected arm.",
        "",
        "## Context size",
        "",
        "Deterministic and free to reproduce: no agent, no network, no quota.",
        "",
        context_table(report),
        "",
        "## Accuracy",
        "",
        f"{trials} trials per cell, a fresh agent process each time. The interval is a",
        "95% percentile bootstrap, so no distribution is assumed.",
        "",
        accuracy_table(report),
        "",
        "The middle row is the one that keeps this honest. An agent given no injected",
        "context, but left free to open `.continuum/` itself, answers just as well.",
        "Recording the context is what produces the accuracy. Injecting it is what",
        "makes it cheap, which is the next table.",
        "",
        "## Time to answer",
        "",
        timing_table(report),
        "",
        "## Trials that completed",
        "",
        "An agent that fails to start has not answered badly, it has not answered.",
        "Those trials are excluded from accuracy and counted here instead.",
        "",
        completion_table(report),
        "",
    ]
    conflict = conflict_table(report)
    if conflict:
        parts += [
            "## Which source is used when they disagree",
            "",
            "The injected context and the files on disk are made to contradict each",
            "other: the injected version says the class is `BillingGateway`, the files",
            "say `LedgerClient`.",
            "",
            conflict,
            "",
        ]
    categories = category_table(report)
    if categories:
        parts += [
            "## Categories",
            "",
            "The distinctions LongMemEval draws, since recall alone flatters a memory",
            "system.",
            "",
            categories,
            "",
        ]
    delegation = delegation_table(report)
    if delegation:
        parts += ["## Delegation", "", "One agent consulting another.", "", delegation, ""]

    parts += [
        "## What this does not measure",
        "",
        "One scenario, one project. A benchmark built from a single recorded state",
        "cannot tell you how Continuum behaves across a real project's history.",
        "",
        "Substring matching against accepted answers, per question. It is",
        "reproducible and needs no judge, but it cannot tell a correct answer from a",
        "differently-worded correct answer it was not told to accept.",
        "",
        "Gemini is absent: it stops on a browser sign-in prompt before answering, so",
        "there is nothing to measure without a signed-in machine.",
        "",
        "## Faults this benchmark has had",
        "",
        "Recorded because a benchmark that hides its own failures is worth less than",
        "one that does not.",
        "",
        "1. The scorer matched each accepted answer against the whole reply rather",
        "   than the question it belonged to, so an answer to one question could",
        "   satisfy another. Every accuracy figure it produced was withdrawn.",
        "2. A conflict test built its injected prompt from the same files it was",
        "   meant to contradict, so both sides agreed and the result meant nothing.",
        "3. A control arm scored an agent's refusal to start as zero, turning an",
        "   infrastructure failure into a model result.",
        "4. Two runs were left calling the same CLIs at once, so a set of timings was",
        "   measured under contention and had to be discarded and repeated.",
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default=str(Path(__file__).resolve().parent / "results.json"))
    parser.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "docs" / "benchmarks.md"))
    parser.add_argument("--write", action="store_true", help="Write the file instead of printing it.")
    args = parser.parse_args()

    report = json.loads(Path(args.results).read_text(encoding="utf-8"))
    text = render(report)
    if args.write:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
        summary = headline(report)
        if summary:
            print(f"headline: {summary}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
