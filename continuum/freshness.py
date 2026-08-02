"""Whether the recorded context still describes the code it was written about.

A handoff is a claim about a moment. Git commits move on, branches get rewound,
work gets rebased, and none of that reaches `current.md`, so a note saying
"next: fix the failing retry test" survives long after the test was fixed and
still reads as current. Continuum's own `.continuum/current.md` spent five
merged pull requests telling every agent to review a pull request that had
already merged.

Recording the commit a handoff was written against, then comparing it on read,
turns that from a silent lie into a visible one. Nothing here guesses: when the
project is not a Git repository, or the handoff predates this, there is no
recorded commit and nothing is claimed.

`evidence.py` already does the same comparison for a stale test gate, so this is
that pattern applied to handoffs.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime
    from .core import MemoryStore

SHORT = 7


def _git(project: Path, *args: str) -> str | None:
    try:
        finished = subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        # Provenance is best-effort metadata. Recording a handoff must not fail
        # because Git was missing, slow, or replaced by a test double, so every
        # failure here means "unknown" rather than an exception on the save path.
        return None
    if finished.returncode != 0:
        return None
    return finished.stdout.strip() or None


def _ok(project: Path, *args: str) -> bool:
    """Whether the command succeeded. For git commands that answer by exit code
    and print nothing, which `_git` cannot distinguish from failure."""
    try:
        return subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True, text=True, timeout=10,
        ).returncode == 0
    except Exception:  # see _git: never fail a save over a Git probe
        return False


def _known(project: Path, commit: str) -> bool:
    """Whether the commit is still an object in this repository."""
    return _ok(project, "cat-file", "-e", f"{commit}^{{commit}}")


def head_sha(project: Path) -> str | None:
    """The commit the project is on, or None when it is not a Git repository."""
    return _git(project, "rev-parse", "HEAD")


def recorded_commit(store: "MemoryStore") -> str | None:
    """The commit the newest handoff was written against, if it recorded one."""
    for event in store.recent_handoffs(1):
        payload: dict[str, Any] = event.get("payload") or {}
        commit = payload.get("commit")
        return str(commit) if commit else None
    return None


def age_days(store: "MemoryStore") -> int | None:
    """Whole days since the handoff was written, or None if none exists."""
    handoff = store.state_dir / "latest_handoff.md"
    if not handoff.exists():
        return None
    seconds = time.time() - handoff.stat().st_mtime
    return int(seconds // 86_400)


def age_note(store: "MemoryStore") -> str | None:
    """How old the context is, for readers that cannot see a file's timestamp.

    The status card prints an age because a person is looking at a terminal. An
    agent receiving injected context sees present-tense prose and nothing else,
    so a month-old next step reads exactly like this morning's. This is the one
    freshness signal available whether or not the project uses Git.
    """
    days = age_days(store)
    if not days:
        return None
    return f"Recorded {days} day{'s' if days > 1 else ''} ago."


def describe(store: "MemoryStore") -> str | None:
    """One line on how far the recorded context has drifted, or None.

    None means there is nothing to say rather than that everything is current:
    a project outside Git, or a handoff written before commits were recorded,
    cannot be checked and must not be reported as fresh.
    """
    recorded = recorded_commit(store)
    if not recorded:
        return None
    current = head_sha(store.project)
    if not current or current == recorded:
        return None
    short = recorded[:SHORT]
    if not _known(store.project, recorded):
        return (
            f"Recorded against commit {short}, which is no longer in this "
            "repository. This context may describe work that no longer exists."
        )
    # Ancestry first. `A..B` counts commits reachable from B and not from A, and
    # on a sibling branch that count is positive even though B never descended
    # from A. Reporting that as "3 commits ago" would describe a divergence as
    # ordinary progress.
    if not _ok(store.project, "merge-base", "--is-ancestor", recorded, "HEAD"):
        return (
            f"Recorded against commit {short}, which is not an ancestor of the "
            "current commit. The branch was reset, rebased, or switched, so this "
            "context may describe work that no longer exists."
        )
    ahead = _git(store.project, "rev-list", "--count", f"{recorded}..HEAD")
    if ahead is None or ahead == "0":
        return None
    count = int(ahead)
    commits = "commit" if count == 1 else "commits"
    return (
        f"Recorded against commit {short}, {count} {commits} ago. "
        "Check it still describes the code before continuing from it."
    )
