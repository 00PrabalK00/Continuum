"""Claims that say what kind of claim they are.

Everything Continuum recorded used to arrive at the next agent flattened into
one voice. "We chose PostgreSQL over MySQL" and "the retry test probably fails
on the timeout" read identically in a handoff, so a guess someone made on
Tuesday came back on Friday as something the project had settled.

Three kinds, because they behave differently rather than because three is a
tidy number:

- A decision was made. It stands until something replaces it.
- A hypothesis is being tested. It is open until confirmed or dropped, and an
  agent should treat it as a question rather than as a starting point.
- A fact was observed. It was true of the code at the commit it was recorded
  against, which is why the commit is recorded with it.

A hypothesis that is never resolved is the interesting case. Left alone it
looks more certain every time it is carried forward, so open ones are shown as
open and carry the date they were raised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .freshness import head_sha

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime
    from .core import MemoryStore

KINDS = ("decision", "hypothesis", "fact")
OPEN, CONFIRMED, DROPPED = "open", "confirmed", "dropped"


class ClaimError(ValueError):
    """A claim was asked for that does not exist, or a kind that is not one."""


def record(store: "MemoryStore", kind: str, text: str, source: str = "cli") -> dict[str, Any]:
    kind = (kind or "").strip().lower()
    text = (text or "").strip()
    if kind not in KINDS:
        raise ClaimError(f"{kind!r} is not a claim kind. Use one of: {', '.join(KINDS)}.")
    if not text:
        raise ClaimError("Say what the claim is.")
    payload = {
        "type": kind,
        "text": text,
        "state": OPEN if kind == "hypothesis" else "",
        "source": source,
        "commit": head_sha(store.project),
        "branch": store.current_branch(),
    }
    store.event("claim", payload)
    return payload


def recent(store: "MemoryStore", limit: int = 200) -> list[dict[str, Any]]:
    """Claims on the current branch, newest first, with their resolutions applied."""
    branch = store.current_branch()
    found, resolutions = [], {}
    for item in store.recent_events(1_000):
        payload = item.get("payload") or {}
        if item.get("kind") == "claim_resolved":
            resolutions[int(payload.get("claim_id", 0))] = payload.get("state")
        elif item.get("kind") == "claim":
            if (payload.get("branch") or store.DEFAULT_BRANCH) == branch:
                found.append({"id": item["id"], "created_at": item["created_at"], **payload})
    for item in found:
        if item["id"] in resolutions:
            item["state"] = resolutions[item["id"]]
    found.reverse()
    return found[:limit]


def find(store: "MemoryStore", claim_id: int) -> dict[str, Any]:
    for item in recent(store, 1_000):
        if item["id"] == claim_id:
            return item
    raise ClaimError(f"No claim {claim_id}. `continuum note` lists them.")


def resolve(store: "MemoryStore", claim_id: int, state: str) -> dict[str, Any]:
    """Confirm or drop a hypothesis.

    Recorded as its own event rather than by editing the original, so the log
    still says the hypothesis was raised and when it stopped being open. A
    hypothesis quietly rewritten into a fact loses the fact that anyone doubted
    it.
    """
    claim = find(store, claim_id)
    if claim["type"] != "hypothesis":
        raise ClaimError(f"Claim {claim_id} is a {claim['type']}, and only a hypothesis is resolved.")
    if state not in (CONFIRMED, DROPPED):
        raise ClaimError(f"{state!r} is not a resolution. Use confirmed or dropped.")
    store.event("claim_resolved", {"claim_id": claim_id, "state": state,
                                   "commit": head_sha(store.project)})
    return {**claim, "state": state}


def open_questions(store: "MemoryStore") -> list[dict[str, Any]]:
    return [item for item in recent(store) if item["type"] == "hypothesis" and item["state"] == OPEN]


def decisions(store: "MemoryStore") -> list[dict[str, Any]]:
    return [item for item in recent(store) if item["type"] == "decision"]


def render(store: "MemoryStore") -> str:
    found = recent(store)
    if not found:
        return (
            "Nothing recorded yet. `continuum note decision \"chose PostgreSQL\"` "
            "records one, and hypothesis and fact are the other two kinds."
        )
    lines = []
    for item in found:
        state = f" [{item['state']}]" if item.get("state") else ""
        stamp = str(item["created_at"])[:10]
        lines.append(f"{item['id']:<5} {stamp}  {item['type']:<10}{state} {item['text']}")
    return "\n".join(lines)


def for_context(store: "MemoryStore", limit: int = 3) -> str:
    """The short block that goes to an agent, or nothing.

    Bounded on purpose. This is prepended to context that is deliberately small,
    so an unbounded list of everything ever decided would defeat the compaction
    it is attached to.
    """
    parts = []
    settled = decisions(store)[:limit]
    if settled:
        parts.append("Decisions: " + "; ".join(item["text"] for item in settled))
    questions = open_questions(store)[:limit]
    if questions:
        parts.append(
            "Open questions, not settled: "
            + "; ".join(item["text"] for item in questions)
        )
    return "\n".join(parts)
