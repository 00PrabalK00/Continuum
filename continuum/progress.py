"""One way to record progress, shared by every surface that records it.

`continuum save`, the MCP tools and the session-end hook all write a handoff,
and each used to assemble the event payload itself. They drifted: only the
session-end path carried `base_next_step`, so the guard that stops a
carried-forward next step being annotated twice was bypassed by every other
route, and the annotation compounded on each cycle.

Putting the write in one place is also what makes the tool safe to hand to an
agent. An agent asked to "save my progress" should get the same record a person
typing `continuum save` gets, including the summary a local model can write when
neither the agent nor the user supplied one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .handoff_llm import generate_handoff, read_handoff_model
from .providers import ProviderError

if TYPE_CHECKING:
    from .core import MemoryStore


def base_next_step(store: "MemoryStore") -> str | None:
    """The last next step as originally written, before any annotation.

    Continuum marks a carried-forward next step as unconfirmed when a session
    changed files. Rebuilding from the original rather than the annotated text
    is what stops those marks stacking.
    """
    for item in reversed(store.recent_handoffs(60)):
        payload = item["payload"]
        base = payload.get("base_next_step") or payload.get("next_step")
        if base:
            return str(base)
    return None


def record_progress(
    store: "MemoryStore",
    task: str = "",
    next_step: str = "",
    *,
    source: str = "agent",
) -> dict[str, Any]:
    """Record a handoff, filling in whatever the caller did not supply.

    With both fields given, this records them. With neither, it asks the
    configured handoff model to summarise what has been recorded, and failing
    that carries the last recorded task forward. Raises ValueError only when
    there is genuinely nothing to record.
    """
    if not store.config_file.exists():
        from .core import DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD

        store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)

    task = (task or "").strip()
    next_step = (next_step or "").strip()
    summarized_by = None

    if not task:
        try:
            generated = generate_handoff(store)
        except ProviderError:
            generated = None
        if generated:
            task, generated_next = generated
            next_step = next_step or generated_next
            selected = read_handoff_model(store) or {}
            summarized_by = selected.get("provider")
            if selected.get("model"):
                summarized_by = f"{summarized_by}:{selected['model']}"
        else:
            latest = store.latest_task()
            if not latest:
                raise ValueError(
                    "Nothing to record yet. Describe what you were doing, for example: "
                    'task="fixed the auth bug", next_step="test the retry logic".'
                )
            task = latest[0]
            next_step = next_step or (latest[1] or "")

    base = base_next_step(store) or next_step or None
    store.event(
        "handoff",
        {
            "task": task,
            "next_step": next_step or None,
            "base_next_step": base,
            "source": source,
        },
    )
    store.write_handoff(task, next_step or None)

    message = f"Recorded: {task}"
    if next_step:
        message += f"\nNext: {next_step}"
    if summarized_by:
        message += f"\nSummary written by {summarized_by}."
    return {
        "task": task,
        "next_step": next_step or None,
        "summarized_by": summarized_by,
        "message": message,
    }
