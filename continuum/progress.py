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
    """The most recent next step as originally written, before any annotation.

    Continuum marks a carried-forward next step as unconfirmed when a session
    changed files. Rebuilding from the original rather than the annotated text
    is what stops those marks stacking.

    `recent_handoffs` is already newest-first, so this walks it directly. An
    earlier version reversed it and returned the oldest retained action, which
    would have resurrected a step abandoned long ago.
    """
    for item in store.recent_handoffs(60):
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
    model_error = None
    # Whether the next step being recorded is a fresh statement of intent or the
    # previous one carried forward. A fresh one becomes the new base; a carried
    # one must keep the original base, or the annotation it may already carry
    # would become the thing future sessions annotate.
    next_step_is_new = bool(next_step)

    if not task or not next_step:
        try:
            generated = generate_handoff(store)
        except ProviderError as error:
            # A configured model that did not answer has to be reported. Silently
            # recording the previous state instead looks identical to a summary,
            # so the user would believe the model wrote it.
            generated, model_error = None, str(error)
        if generated:
            generated_task, generated_next = generated
            task = task or generated_task
            if not next_step:
                next_step, next_step_is_new = generated_next, True
            selected = read_handoff_model(store) or {}
            summarized_by = selected.get("provider")
            if selected.get("model"):
                summarized_by = f"{summarized_by}:{selected['model']}"
        else:
            latest = store.latest_task()
            if not latest and not task:
                raise ValueError(
                    "Nothing to record yet. Describe what you were doing, for example: "
                    'task="fixed the auth bug", next_step="test the retry logic".'
                )
            if latest:
                task = task or latest[0]
                if not next_step:
                    # Carried forward, so the original base is preserved below.
                    next_step = latest[1] or ""

    base = next_step if next_step_is_new else (base_next_step(store) or next_step or None)
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
    elif model_error:
        message += (
            f"\nThe configured handoff model did not answer ({model_error}), "
            "so this is the state Continuum already had."
        )
    return {
        "task": task,
        "next_step": next_step or None,
        "summarized_by": summarized_by,
        "model_error": model_error,
        "message": message,
    }
