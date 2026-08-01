"""Optional dedicated LLM for handoff and context creation.

A third model, separate from the coding agents, configured per project in
`.continuum/config.json` under the `handoff_model` key. When set, it writes
the handoff in two places:

- `continuum save` with no text: summarize recorded session material into a
  task and next step instead of reusing the previous handoff.
- Context checkpoint: when a wrapped agent reaches its estimated context
  limit, the session tail is summarized into a real continuation handoff
  instead of a synthetic placeholder.

Every failure falls back to the existing non-LLM behavior; the handoff LLM
is never required for a session to complete.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .core import compact_text, utc_now, write_text
from .providers import ProviderError, ProviderManager

if TYPE_CHECKING:
    from .core import MemoryStore

CONFIG_KEY = "handoff_model"
MATERIAL_BUDGET = 8_000


def read_handoff_model(store: "MemoryStore") -> dict[str, str] | None:
    config = store.read_config()
    entry = config.get(CONFIG_KEY)
    if not isinstance(entry, dict) or not entry.get("provider"):
        return None
    return {"provider": str(entry["provider"]), "model": str(entry.get("model") or "")}


def write_handoff_model(store: "MemoryStore", provider: str | None, model: str | None = None) -> None:
    config = store.read_config()
    if provider is None:
        config.pop(CONFIG_KEY, None)
    else:
        config[CONFIG_KEY] = {"provider": provider, "model": model or ""}
    config["updated_at"] = utc_now()
    write_text(store.config_file, json.dumps(config, indent=2) + "\n")


def gather_material(store: "MemoryStore", session_tail: list[str] | None = None) -> str:
    sections: list[str] = []
    current = store.state_dir / "current.md"
    if current.exists():
        sections.append("## Previous Recorded State\n" + current.read_text(encoding="utf-8"))
    changes = store.git_or_watch_changes()
    if changes:
        sections.append("## Changed Files\n" + "\n".join(f"- {item}" for item in changes))
    events = store.recent_events(20)
    if events:
        lines = [
            f"- {item['created_at']} {item['kind']}: {compact_text(str(item['payload'].get('summary') or item['payload'].get('task') or ''), 200)}"
            for item in events
        ]
        sections.append("## Recent Events\n" + "\n".join(lines))
    if session_tail:
        sections.append("## Session Output Tail\n```text\n" + "".join(session_tail) + "\n```")
    return compact_text("\n\n".join(sections), MATERIAL_BUDGET)


def parse_reply(reply: str) -> tuple[str, str] | None:
    task = ""
    next_step = ""
    for line in reply.splitlines():
        stripped = line.strip().lstrip("*#- ").strip()
        upper = stripped.upper()
        if upper.startswith("TASK:"):
            task = stripped[5:].strip().lstrip("*").strip()
        elif upper.startswith("NEXT:"):
            next_step = stripped[5:].strip().lstrip("*").strip()
    if task and next_step:
        return compact_text(task, 360), compact_text(next_step, 300)
    return None


def generate_handoff(store: "MemoryStore", session_tail: list[str] | None = None) -> tuple[str, str] | None:
    """Ask the configured handoff LLM for a task/next-step pair.

    Returns None when no handoff model is configured. Raises ProviderError on
    provider failure so callers can fall back explicitly.
    """
    selected = read_handoff_model(store)
    if not selected:
        return None
    material = gather_material(store, session_tail)
    if not material.strip():
        raise ProviderError("No recorded session material to summarize yet.")
    prompt = (
        "You are the handoff scribe for a software work session. From the recorded "
        "material below, write a continuation handoff for the next AI assistant.\n"
        "Reply with exactly two lines and nothing else:\n"
        "TASK: <one sentence: what was being worked on and its current state>\n"
        "NEXT: <one sentence: the exact next action to take>\n\n"
        + material
    )
    manager = ProviderManager(store.state_dir, store)
    reply = manager.ask(selected["provider"], prompt, selected["model"] or None)
    parsed = parse_reply(reply)
    if parsed is None:
        raise ProviderError("Handoff model reply did not contain TASK: and NEXT: lines.")
    return parsed
