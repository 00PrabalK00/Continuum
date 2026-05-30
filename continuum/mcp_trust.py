"""MCP trust registry for Continuum (`§Phase3.11` / issue #33).

Stores per-server trust posture in `.continuum/mcp_trust.json` so Continuum can
decide which external MCP servers and tools are allowed. The MCP spec says tool
annotations from a server must be treated as untrusted unless that server is
itself trusted; this module encodes that as default-untrusted for any server not
present in the registry.

Registry shape::

    {
      "version": 1,
      "servers": [
        {
          "server": "acme-tools",
          "status": "trusted" | "untrusted" | "blocked",
          "tool_allow": ["read_file", ...],
          "tool_deny": ["delete_all", ...],
          "risk_score": 0,
          "last_changed": "2026-05-30T12:00:00+00:00"
        }
      ]
    }

Gating precedence in `is_tool_allowed`: deny > allow > status default. A tool on
a server's `tool_deny` list is never allowed. A tool on `tool_allow` is allowed
regardless of status (explicit allow-list overrides untrusted/blocked). Otherwise
only a `trusted` server's tools are allowed by default; `untrusted`/`blocked`
servers (and unknown servers) deny by default.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import utc_now

TRUST_FILENAME = "mcp_trust.json"
STATUSES = ("trusted", "untrusted", "blocked")
DEFAULT_STATUS = "untrusted"


class TrustError(ValueError):
    """Raised when the trust registry is malformed or an operation is invalid."""


def trust_path(state_dir: Path) -> Path:
    return state_dir / TRUST_FILENAME


def _empty_registry() -> dict[str, Any]:
    return {"version": 1, "servers": []}


def _validate_entry(entry: dict[str, Any]) -> dict[str, Any]:
    server = entry.get("server")
    if not isinstance(server, str) or not server.strip():
        raise TrustError("Each trust entry needs a non-empty `server` string.")
    status = entry.get("status", DEFAULT_STATUS)
    if status not in STATUSES:
        raise TrustError(f"Invalid status `{status}` for server `{server}`. One of: {', '.join(STATUSES)}.")
    for key in ("tool_allow", "tool_deny"):
        value = entry.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise TrustError(f"`{key}` for server `{server}` must be a list of strings.")
    risk = entry.get("risk_score", 0)
    if not isinstance(risk, int) or isinstance(risk, bool):
        raise TrustError(f"`risk_score` for server `{server}` must be an integer.")
    return {
        "server": server.strip(),
        "status": status,
        "tool_allow": sorted({item for item in entry.get("tool_allow", []) if item.strip()}),
        "tool_deny": sorted({item for item in entry.get("tool_deny", []) if item.strip()}),
        "risk_score": risk,
        "last_changed": str(entry.get("last_changed") or utc_now()),
    }


def validate_registry(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise TrustError("Trust registry must be a JSON object.")
    servers = data.get("servers", [])
    if not isinstance(servers, list):
        raise TrustError("Trust registry `servers` must be a list.")
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in servers:
        if not isinstance(entry, dict):
            raise TrustError("Each trust entry must be a JSON object.")
        clean = _validate_entry(entry)
        if clean["server"] in seen:
            raise TrustError(f"Duplicate server entry: {clean['server']}.")
        seen.add(clean["server"])
        validated.append(clean)
    return {"version": int(data.get("version", 1)), "servers": validated}


def load_registry(state_dir: Path) -> dict[str, Any]:
    """Load the trust registry, returning an empty registry when absent."""
    path = trust_path(state_dir)
    if not path.exists():
        return _empty_registry()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise TrustError(f"Trust registry is not valid JSON: {error}") from error
    except OSError as error:
        raise TrustError(f"Could not read trust registry: {error}") from error
    return validate_registry(data)


def save_registry(state_dir: Path, registry: dict[str, Any]) -> Path:
    registry = validate_registry(registry)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = trust_path(state_dir)
    path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    return path


def find_entry(registry: dict[str, Any], server: str) -> dict[str, Any] | None:
    for entry in registry.get("servers", []):
        if entry["server"] == server:
            return entry
    return None


def is_tool_allowed(registry: dict[str, Any], server: str, tool: str) -> bool:
    """Decide whether `tool` on `server` may be used.

    Precedence: deny > allow > status default. Unknown servers are
    default-untrusted and therefore deny by default unless the tool is on an
    explicit allow-list (which cannot exist for an unknown server, so they deny).
    """
    entry = find_entry(registry, server)
    if entry is None:
        # Unknown server: default-untrusted per MCP guidance.
        return False
    if tool in entry["tool_deny"]:
        return False
    if tool in entry["tool_allow"]:
        return True
    return entry["status"] == "trusted"


# --- mutations: each is recorded by the caller as an `mcp_trust_changed` audit
# --- event. These helpers stamp `last_changed` and return the updated registry.


def _upsert(registry: dict[str, Any], server: str) -> dict[str, Any]:
    entry = find_entry(registry, server)
    if entry is None:
        entry = {
            "server": server,
            "status": DEFAULT_STATUS,
            "tool_allow": [],
            "tool_deny": [],
            "risk_score": 0,
            "last_changed": utc_now(),
        }
        registry["servers"].append(entry)
    return entry


def add_server(registry: dict[str, Any], server: str, status: str = DEFAULT_STATUS) -> dict[str, Any]:
    server = (server or "").strip()
    if not server:
        raise TrustError("A server name is required.")
    if status not in STATUSES:
        raise TrustError(f"Invalid status: {status}. One of: {', '.join(STATUSES)}.")
    if find_entry(registry, server) is not None:
        raise TrustError(f"Server already registered: {server}. Use `set` to change it.")
    entry = _upsert(registry, server)
    entry["status"] = status
    entry["last_changed"] = utc_now()
    return entry


def set_status(registry: dict[str, Any], server: str, status: str) -> dict[str, Any]:
    if status not in STATUSES:
        raise TrustError(f"Invalid status: {status}. One of: {', '.join(STATUSES)}.")
    entry = _upsert(registry, (server or "").strip())
    entry["status"] = status
    entry["last_changed"] = utc_now()
    return entry


def allow_tool(registry: dict[str, Any], server: str, tool: str) -> dict[str, Any]:
    tool = (tool or "").strip()
    if not tool:
        raise TrustError("A tool name is required.")
    entry = _upsert(registry, (server or "").strip())
    entry["tool_deny"] = [item for item in entry["tool_deny"] if item != tool]
    if tool not in entry["tool_allow"]:
        entry["tool_allow"] = sorted([*entry["tool_allow"], tool])
    entry["last_changed"] = utc_now()
    return entry


def deny_tool(registry: dict[str, Any], server: str, tool: str) -> dict[str, Any]:
    tool = (tool or "").strip()
    if not tool:
        raise TrustError("A tool name is required.")
    entry = _upsert(registry, (server or "").strip())
    entry["tool_allow"] = [item for item in entry["tool_allow"] if item != tool]
    if tool not in entry["tool_deny"]:
        entry["tool_deny"] = sorted([*entry["tool_deny"], tool])
    entry["last_changed"] = utc_now()
    return entry


def remove_server(registry: dict[str, Any], server: str) -> bool:
    server = (server or "").strip()
    before = len(registry.get("servers", []))
    registry["servers"] = [entry for entry in registry["servers"] if entry["server"] != server]
    return len(registry["servers"]) < before
