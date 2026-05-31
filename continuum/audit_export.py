"""Auditable event-log export for Continuum (`§Gap5`).

Exports the local event log as an inspectable trail. Each entry carries the
event id, time, kind and a compact JSON payload. Secret VALUES are never present
to begin with -- the egress scrubber only ever records counts and types in
`secret_redacted` events -- so the export contains no redacted secret material.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from .core import MemoryStore, compact_text

_RELATIVE_DAYS = re.compile(r"^(\d+)\s*d$", re.I)


def parse_since(value: str | None) -> str | None:
    """Parse `--since` into an ISO-8601 lower bound, or None when absent.

    Accepts an ISO timestamp/date (e.g. "2026-05-01" or "2026-05-01T00:00:00")
    or a relative "<N>d" form (e.g. "7d" = 7 days ago). Raises ValueError on
    anything else so the CLI can report a clear error.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    match = _RELATIVE_DAYS.match(value)
    if match:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=int(match.group(1)))
        return cutoff.replace(microsecond=0).isoformat()
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(
            f"Invalid --since value: {value!r}. Use an ISO date/time or a relative form like '7d'."
        ) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.isoformat()


def collect_events(store: MemoryStore, since: str | None = None, limit: int = 100_000) -> list[dict[str, Any]]:
    """Return audit entries (oldest first) filtered by an optional ISO lower bound."""
    events = store.recent_events(limit)
    entries: list[dict[str, Any]] = []
    for event in events:
        if since is not None and str(event["created_at"]) < since:
            continue
        entries.append(
            {
                "id": event["id"],
                "time": event["created_at"],
                "kind": event["kind"],
                "payload": event["payload"],
            }
        )
    return entries


def _payload_summary(payload: dict[str, Any]) -> str:
    return compact_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), 400)


def render_json(entries: list[dict[str, Any]]) -> str:
    return json.dumps(entries, ensure_ascii=True, indent=2)


def render_markdown(entries: list[dict[str, Any]]) -> str:
    lines = [
        "# Continuum Audit Trail",
        "",
        f"Exported: {dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()}",
        f"Events: {len(entries)}",
        "",
        "| ID | Time | Kind | Payload |",
        "| --- | --- | --- | --- |",
    ]
    for entry in entries:
        payload = _payload_summary(entry["payload"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| M{entry['id']} | {entry['time']} | {entry['kind']} | {payload} |")
    if not entries:
        lines.append("| - | - | - | No events in range. |")
    return "\n".join(lines) + "\n"


def export(store: MemoryStore, since: str | None = None, fmt: str = "md") -> str:
    """Build the audit export text in `json` or `md` format."""
    if fmt not in {"json", "md"}:
        raise ValueError(f"Invalid export format: {fmt}. Use 'json' or 'md'.")
    entries = collect_events(store, parse_since(since))
    return render_json(entries) if fmt == "json" else render_markdown(entries)
