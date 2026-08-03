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
            "branch": store.current_branch(),
        },
    )
    store.write_handoff(task, next_step or None)
    return {"task": task, "next_step": next_step}


def switch(store: "MemoryStore", name: str) -> dict[str, Any]:
    """Move to a branch, and make the files an agent reads follow it.

    `current.md` and `latest_handoff.md` are what a launched agent is actually
    given. Changing only the recorded branch would leave those holding the
    previous branch's work, so `continuum go` would hand over the wrong context
    while the status card claimed otherwise.

    A branch that does not exist yet starts from where you are, the way `git
    branch` points at the current commit. That start is recorded as a checkpoint
    on the new branch rather than inferred later, so the branch has a readable
    head from the moment it exists.
    """
    known = store.branches()
    previous = store.current_branch()
    inherited = store.latest_task()
    store.set_branch(name)
    created = name not in known
    if created and inherited:
        store.event(
            "handoff",
            {
                "task": inherited[0],
                "next_step": inherited[1],
                "base_next_step": inherited[1],
                "source": "branch",
                "branched_from": previous,
                "commit": head_sha(store.project),
                "branch": name,
            },
        )
    latest = store.latest_task()
    if latest:
        store.write_handoff(latest[0], latest[1])
    return {"branch": name, "created": created, "from": previous, "latest": latest}


class MergeConflict(HistoryError):
    """Two branches changed the same thing since they diverged."""

    def __init__(self, report: str, fields: list[str]) -> None:
        super().__init__(report)
        self.fields = fields


def fork_point(store: "MemoryStore", branch: str) -> dict[str, Any] | None:
    """The checkpoint a branch started from, if it recorded one."""
    for item in reversed(store.recent_handoffs(500, branch=branch)):
        if field(item, "source") == "branch":
            return item
    return None


def merge_base(store: "MemoryStore", current: str, other: str) -> dict[str, Any] | None:
    """What both sides should be compared against.

    The fork point until the branches have been merged once. After that it is
    the checkpoint that merge took, because comparing against the original fork
    forever reports a field as changed on both sides when only one of them has
    moved since they were last reconciled. That produces a conflict where there
    is none, and --theirs on a false conflict overwrites newer state with older.
    """
    base = fork_point(store, other) or fork_point(store, current)
    for item in store.recent_handoffs(500, branch=current):
        if field(item, "source") == "merge" and field(item, "merged_from") == other:
            taken = (item.get("payload") or {}).get("merged_checkpoint")
            merged = store.get_memory(int(taken)) if taken else None
            if merged and (base is None or merged["id"] > base["id"]):
                return merged
            break
    return base


def merge(store: "MemoryStore", other: str, force: bool = False) -> dict[str, Any]:
    """Bring another branch's state onto this one, or refuse and say why.

    Last write wins is what Continuum did before, and it is the behaviour a
    version control system exists to refuse: two agents assert different things
    and the newer one silently erases the older. So this compares both sides
    against the point they diverged, and where both moved the same field it
    stops and prints them rather than picking.

    A field only one side changed is taken without asking, which is the ordinary
    case and needs no ceremony.
    """
    current = store.current_branch()
    if other == current:
        raise HistoryError(f"{other} is the current branch.")
    if other not in store.branches():
        raise HistoryError(f"No branch {other}. `continuum branch` lists them.")
    theirs = store.recent_handoffs(1, branch=other)
    if not theirs:
        raise HistoryError(f"Branch {other} has no checkpoints to merge.")
    theirs = theirs[0]
    ours = store.recent_handoffs(1, branch=current)
    ours = ours[0] if ours else None
    base = merge_base(store, current, other)

    taken, clashes = {}, []
    for name in ("task", "next_step"):
        mine = field(ours, name) if ours else ""
        yours = field(theirs, name)
        origin = field(base, name) if base else ""
        if mine == yours:
            taken[name] = yours
        elif mine == origin:
            taken[name] = yours
        elif yours == origin:
            taken[name] = mine
        else:
            clashes.append((name, mine, yours))
            taken[name] = yours if force else mine

    if clashes and not force:
        lines = [f"{other} and {current} both changed the same thing since they diverged."]
        for name, mine, yours in clashes:
            heading = "Task" if name == "task" else "Next step"
            lines += ["", heading, f"  {current}: {mine}", f"  {other}: {yours}"]
        lines += [
            "",
            "Nothing was recorded. Decide which is right and save it, or re-run",
            f"with --theirs to take {other}.",
        ]
        raise MergeConflict("\n".join(lines), [name for name, _, _ in clashes])

    store.event(
        "handoff",
        {
            "task": taken["task"],
            "next_step": taken["next_step"] or None,
            "base_next_step": taken["next_step"] or None,
            "source": "merge",
            "merged_from": other,
            "merged_checkpoint": theirs["id"],
            "resolved": [name for name, _, _ in clashes] or None,
            "commit": head_sha(store.project),
            "branch": current,
        },
    )
    store.write_handoff(taken["task"], taken["next_step"] or None)
    return {"branch": current, "from": other, "resolved": [n for n, _, _ in clashes], **taken}


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
