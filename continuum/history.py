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

from .freshness import head_sha

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
    # By id rather than by scanning a window. A fixed scan rejects checkpoints
    # that `continuum log --limit` will happily display, so history the project
    # still holds would be unreachable to diff and restore.
    found = store.get_memory(wanted)
    if not found:
        raise HistoryError(f"No checkpoint C{wanted}. Run `continuum log` to see which exist.")
    if found.get("kind") != "handoff":
        raise HistoryError(
            f"M{wanted} is a {found.get('kind')} event, not a checkpoint. "
            "`continuum log` lists the checkpoints."
        )
    return found


def label(item: dict[str, Any]) -> str:
    return f"C{item['id']}"


def field(item: dict[str, Any], name: str) -> str:
    return str((item.get("payload") or {}).get(name) or "")


def one_line(text: str, limit: int = 100) -> str:
    """Flatten and bound a recorded field for the log.

    A task is caller-supplied and can be long or multiline. Printed raw, one
    checkpoint turns a twenty-line history into thousands of characters, and the
    line-per-checkpoint shape the log depends on stops holding.
    """
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def restore(store: "MemoryStore", wanted: dict[str, Any]) -> dict[str, str]:
    """Record the selected checkpoint's state again, exactly as it was.

    Not through record_progress: its job is to fill in what a caller left out,
    by asking the handoff model or carrying the current next step forward. That
    is right for a save and wrong here. A checkpoint with a task and no next
    step would come back carrying the *current* next step, which is a state that
    never existed, presented as a restore of one that did.
    """
    task = field(wanted, "task")
    next_step = field(wanted, "next_step")
    store.event(
        "handoff",
        {
            "task": task,
            "next_step": next_step or None,
            "base_next_step": next_step or None,
            "source": "restore",
            "restored_from": wanted["id"],
            "commit": head_sha(store.project),
        },
    )
    store.write_handoff(task, next_step or None)
    return {"task": task, "next_step": next_step}


def mentions(item: dict[str, Any], text: str) -> bool:
    wanted = text.lower()
    return any(wanted in field(item, name).lower() for name in ("task", "next_step"))


def blame(store: "MemoryStore", text: str) -> list[dict[str, Any]]:
    """Every checkpoint whose task or next step mentions the text, oldest first.

    The whole history, not a recent window. "First recorded in C3" is a claim
    about everything that came before it, so answering it from the newest N
    checkpoints would report the oldest one in the window as the first mention
    and call anything older than the window unrecorded.

    Deliberately literal otherwise. An agent reading `current.md` is told a
    thing and has no way to ask where it came from, and the useful answer is
    "this checkpoint, on this date, against this commit", not a guess about
    which session probably meant it.
    """
    return [item for item in store.handoffs_mentioning(text) if mentions(item, text)]


def render_blame(store: "MemoryStore", text: str) -> str:
    found = blame(store, text)
    if not found:
        return (
            f"No checkpoint mentions {text!r}. `continuum log` lists what was "
            "recorded, and `continuum search` looks through the whole event log "
            "rather than checkpoints alone."
        )
    first, last = found[0], found[-1]
    lines = [f"{text!r} first recorded in {label(first)}, {stamp(first)}{against(first)}."]
    source = field(first, "source")
    if source:
        lines[-1] = lines[-1][:-1] + f", by {source}."
    if last is not first:
        lines.append(f"Still present in {label(last)}, {stamp(last)}.")
        if len(found) > 2:
            middle = ", ".join(label(item) for item in found[1:-1])
            lines.append(f"Also in {middle}.")
    else:
        lines.append("Recorded once and not repeated since.")
    return "\n".join(lines)


def stamp(item: dict[str, Any]) -> str:
    return str(item["created_at"])[:16].replace("T", " ")


def against(item: dict[str, Any]) -> str:
    commit = field(item, "commit")
    return f", against commit {commit[:SHORT]}" if commit else ""


def render_log(store: "MemoryStore", limit: int = 20) -> str:
    found = checkpoints(store, limit)
    if not found:
        return "No checkpoints recorded yet. `continuum save` writes one."
    lines = []
    for item in found:
        commit = field(item, "commit")
        suffix = f"  ({commit[:SHORT]})" if commit else ""
        task = one_line(field(item, "task")) or "(no task recorded)"
        lines.append(f"{label(item):<6} {stamp(item)}  {task}{suffix}")
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
