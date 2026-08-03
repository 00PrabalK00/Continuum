"""Checkpoints as things you can list, compare and return to.

Continuum already keeps the three layers Git keeps: an append-only event log,
checkpoints taken against it, and a materialized view of the newest one. What it
did not have is any way to look at that history. You could read the current
state and nothing else, so a context that drifted somewhere wrong left you no
way to find where, and no way back.

`log`, `diff` and `restore` are the three that make the history usable. Restore
appends rather than rewrites: recording a checkpoint that carries an older state
forward keeps the log honest about what happened, in the way `git revert`
does and `git reset` does not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime
    from .core import MemoryStore

SHORT = 7


class HistoryError(ValueError):
    """A checkpoint was asked for that does not exist."""


def checkpoints(store: "MemoryStore", limit: int = 20) -> list[dict[str, Any]]:
    """Recorded checkpoints, newest first."""
    return list(store.recent_handoffs(limit))


def find(store: "MemoryStore", reference: str) -> dict[str, Any]:
    """Resolve `C7`, `7`, or `HEAD` to a checkpoint."""
    text = reference.strip()
    if text.upper() == "HEAD":
        found = checkpoints(store, 1)
        if not found:
            raise HistoryError("No checkpoints have been recorded yet.")
        return found[0]
    if text.upper().startswith("C"):
        text = text[1:]
    try:
        wanted = int(text)
    except ValueError:
        raise HistoryError(
            f"{reference!r} is not a checkpoint. Use an id from `continuum log`, "
            "such as C7, or HEAD."
        ) from None
    for item in checkpoints(store, 500):
        if item["id"] == wanted:
            return item
    raise HistoryError(f"No checkpoint C{wanted}. Run `continuum log` to see which exist.")


def label(item: dict[str, Any]) -> str:
    return f"C{item['id']}"


def field(item: dict[str, Any], name: str) -> str:
    return str((item.get("payload") or {}).get(name) or "")


def render_log(store: "MemoryStore", limit: int = 20) -> str:
    found = checkpoints(store, limit)
    if not found:
        return "No checkpoints recorded yet. `continuum save` writes one."
    lines = []
    for item in found:
        commit = field(item, "commit")
        stamp = str(item["created_at"])[:16].replace("T", " ")
        suffix = f"  ({commit[:SHORT]})" if commit else ""
        lines.append(f"{label(item):<6} {stamp}  {field(item, 'task') or '(no task recorded)'}{suffix}")
    return "\n".join(lines)


def render_diff(store: "MemoryStore", older: dict, newer: dict) -> str:
    """What changed between two checkpoints, in the order that matters."""
    lines = [f"{label(older)} -> {label(newer)}"]
    for name, heading in (("task", "Task"), ("next_step", "Next step")):
        before, after = field(older, name), field(newer, name)
        if before == after:
            continue
        lines += ["", heading]
        if before:
            lines.append(f"- {before}")
        if after:
            lines.append(f"+ {after}")
    before_commit, after_commit = field(older, "commit"), field(newer, "commit")
    if before_commit != after_commit:
        lines += ["", "Commit"]
        lines.append(f"- {before_commit[:SHORT] or 'none recorded'}")
        lines.append(f"+ {after_commit[:SHORT] or 'none recorded'}")
    if len(lines) == 1:
        lines.append("")
        lines.append("Nothing changed between these two checkpoints.")
    return "\n".join(lines)
