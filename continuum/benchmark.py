"""Benchmark harness for with-vs-without Continuum comparisons."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import MemoryStore, utc_now, write_text
from .roi import task_metrics


METRIC_KEYS = [
    "tokens_used",
    "failed_attempts",
    "files_touched_outside_scope",
    "tests_run",
    "context_resets",
    "human_corrections",
    "changed_files",
]


def capture_task(store: MemoryStore, task_ref: str, label: str) -> dict[str, Any]:
    metrics = task_metrics(store, task_ref)
    return {"captured_at": utc_now(), "label": label, "source": "continuum_task", "metrics": metrics}


def load_capture(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"Cannot read benchmark capture {path}: {error.strerror or error}") from error
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid benchmark capture JSON in {path}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"Benchmark capture {path} must contain a JSON object, got {type(data).__name__}.")
    return data


def write_capture(path: Path, capture: dict[str, Any]) -> None:
    write_text(path, json.dumps(capture, indent=2) + "\n")


def compare_captures(without: dict[str, Any], with_continuum: dict[str, Any]) -> dict[str, Any]:
    left = _metrics_view(without)
    right = _metrics_view(with_continuum)
    deltas = {}
    for key in METRIC_KEYS:
        deltas[key] = _number(right.get(key)) - _number(left.get(key))
    return {
        "without_label": without.get("label") or left.get("label") or "without-continuum",
        "with_label": with_continuum.get("label") or right.get("label") or "with-continuum",
        "without": left,
        "with_continuum": right,
        "delta": deltas,
        "verdict": _verdict(left, right, deltas),
    }


def render_comparison(comparison: dict[str, Any]) -> str:
    lines = [
        "# Continuum Benchmark Comparison",
        "",
        f"- Without: {comparison['without_label']}",
        f"- With Continuum: {comparison['with_label']}",
        f"- Verdict: {comparison['verdict']}",
        "",
        "| Metric | Without | With Continuum | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    without = comparison["without"]
    with_continuum = comparison["with_continuum"]
    for key in METRIC_KEYS:
        lines.append(f"| {key} | {_number(without.get(key))} | {_number(with_continuum.get(key))} | {comparison['delta'][key]} |")
    lines.append("")
    return "\n".join(lines)


def _metrics_view(capture: dict[str, Any]) -> dict[str, Any]:
    """Return the metrics mapping, falling back to the flat capture.

    Tolerates captures that omit the ``metrics`` key entirely as well as
    captures where ``metrics`` is present but ``None`` or otherwise not a
    mapping (e.g. hand-edited or partially-written JSON).
    """
    metrics = capture.get("metrics")
    if isinstance(metrics, dict):
        return metrics
    return capture


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _verdict(left: dict[str, Any], right: dict[str, Any], deltas: dict[str, float]) -> str:
    wins = 0
    losses = 0
    for key in ("tokens_used", "failed_attempts", "files_touched_outside_scope", "context_resets", "human_corrections"):
        if deltas[key] < 0:
            wins += 1
        elif deltas[key] > 0:
            losses += 1
    if bool(right.get("merge_ready")) and not bool(left.get("merge_ready")):
        wins += 1
    elif bool(left.get("merge_ready")) and not bool(right.get("merge_ready")):
        losses += 1
    if wins > losses:
        return "Continuum improved the measured run."
    if losses > wins:
        return "Continuum regressed the measured run; inspect deltas before using this workflow."
    return "Mixed or neutral result; inspect per-metric deltas."
