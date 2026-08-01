"""Agent CLI registry.

Continuum is not tied to a fixed set of agent CLIs. Three agents ship with
tuned launch specs (`claude`, `codex`, `gemini`) because their prompt-passing
conventions differ. Every other command-line agent — `hermes`, `opencode`,
`aider`, an in-house wrapper — works without any registration: name it once
and Continuum records how to launch it in `.continuum/agents.json`.

A spec describes how the bounded handoff reaches the agent:

- ``arg``        prompt appended as the final positional argument (default)
- ``subcommand`` prompt appended after a fixed subcommand, e.g. ``codex exec``
- ``flag``       prompt passed through a named flag, e.g. ``gemini --prompt``
- ``stdin``      prompt written to the agent's standard input

Unknown agents resolve to the ``arg`` convention, which is what the majority
of CLIs accept. Anything that needs another convention can be described once
with `continuum agent add`.
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING, Any

from .core import utc_now, write_text

if TYPE_CHECKING:
    from .core import MemoryStore

AGENTS_FILE = "agents.json"

INJECT_MODES = ("arg", "subcommand", "flag", "stdin")

BUILTIN_AGENTS: dict[str, dict[str, Any]] = {
    "claude": {
        "command": "claude",
        "inject": "arg",
    },
    "codex": {
        "command": "codex",
        "inject": "subcommand",
        "subcommand": "exec",
    },
    "gemini": {
        "command": "gemini",
        "inject": "flag",
        "flag": "--prompt",
        "prompt_flags": ["-p", "--prompt", "-i", "--prompt-interactive"],
        "default_args": ["--approval-mode", "plan", "--output-format", "text"],
        "suppress": [
            "Warning: 256-color support not detected.",
            "Ripgrep is not available.",
            "[DEP0190] DeprecationWarning",
            "(Use `node --trace-deprecation",
        ],
    },
}

# Historical constant kept for callers that only need the tuned built-ins.
AGENTS = tuple(BUILTIN_AGENTS)


class AgentError(ValueError):
    """Raised when an agent cannot be resolved or described."""


def default_spec(name: str) -> dict[str, Any]:
    """The zero-configuration spec used for any CLI Continuum has not seen."""
    return {"command": name, "inject": "arg"}


def normalize_spec(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    merged = {**default_spec(name), **{key: value for key, value in spec.items() if value is not None}}
    inject = str(merged.get("inject") or "arg")
    if inject not in INJECT_MODES:
        raise AgentError(f"Unknown prompt injection mode for {name}: {inject}. Choose from: {', '.join(INJECT_MODES)}.")
    merged["inject"] = inject
    if inject == "subcommand" and not merged.get("subcommand"):
        raise AgentError(f"Agent {name} uses subcommand injection but no subcommand is set.")
    if inject == "flag" and not merged.get("flag"):
        raise AgentError(f"Agent {name} uses flag injection but no flag is set.")
    merged["command"] = str(merged.get("command") or name)
    return merged


def agents_file(store: "MemoryStore"):
    return store.state_dir / AGENTS_FILE


def read_custom(store: "MemoryStore") -> dict[str, dict[str, Any]]:
    path = agents_file(store)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = data.get("agents")
    if not isinstance(entries, dict):
        return {}
    return {str(name): value for name, value in entries.items() if isinstance(value, dict)}


def read_agents(store: "MemoryStore") -> dict[str, dict[str, Any]]:
    """Built-in specs overlaid with project-local ones."""
    known = {name: dict(spec) for name, spec in BUILTIN_AGENTS.items()}
    for name, spec in read_custom(store).items():
        known[name] = {**known.get(name, default_spec(name)), **spec}
    return {name: normalize_spec(name, spec) for name, spec in known.items()}


def write_agent(store: "MemoryStore", name: str, spec: dict[str, Any] | None) -> None:
    """Persist (or remove, when spec is None) one project-local agent spec."""
    custom = read_custom(store)
    if spec is None:
        custom.pop(name, None)
    else:
        custom[name] = normalize_spec(name, spec)
    write_text(
        agents_file(store),
        json.dumps({"agents": custom, "updated_at": utc_now()}, indent=2) + "\n",
    )


def is_installed(spec: dict[str, Any]) -> bool:
    return shutil.which(str(spec.get("command"))) is not None


def resolve(store: "MemoryStore", name: str, remember: bool = True) -> dict[str, Any]:
    """Return the launch spec for an agent name.

    A name Continuum already knows returns its spec. Any other name that is on
    PATH is adopted with the default convention and remembered, so arbitrary
    agent CLIs work without a registration step.
    """
    known = read_agents(store)
    if name in known:
        return known[name]
    if shutil.which(name) is None:
        installed = sorted(agent for agent, spec in known.items() if is_installed(spec))
        detail = f" Installed agents: {', '.join(installed)}." if installed else ""
        raise AgentError(
            f"Unknown agent CLI: {name}. It is not registered with Continuum and not on PATH.{detail}"
        )
    spec = default_spec(name)
    if remember:
        write_agent(store, name, spec)
    return normalize_spec(name, spec)


def installed_agents(store: "MemoryStore") -> list[str]:
    return sorted(name for name, spec in read_agents(store).items() if is_installed(spec))


def pick_agent(store: "MemoryStore", exclude: str | None = None) -> str:
    """Choose the agent to hand the session to next.

    Prefers an installed agent other than the one that just ran, which is the
    common case: the previous agent ran out of context and work continues
    elsewhere. Falls back to the previous agent when it is the only one.
    """
    installed = installed_agents(store)
    if not installed:
        raise AgentError(
            "No known agent CLI is installed. Name yours once with `continuum go <name>` and "
            "Continuum will remember it; any command on PATH works. Without a CLI, use "
            "`continuum copy` to paste the context into a web chat."
        )
    for name in installed:
        if name != exclude:
            return name
    return installed[0]


def launch_args(spec: dict[str, Any], passthrough: list[str], prompt: str) -> list[str]:
    """Build the agent's argument list with the bounded handoff injected."""
    inject = spec["inject"]
    if inject == "stdin":
        return list(passthrough)
    if inject == "subcommand":
        subcommand = str(spec["subcommand"])
        if passthrough and passthrough[0] == subcommand:
            return [*passthrough, prompt]
        return [subcommand, *passthrough, prompt]
    if inject == "flag":
        return flag_args(spec, passthrough, prompt)
    return [*passthrough, prompt]


def flag_args(spec: dict[str, Any], passthrough: list[str], prompt: str) -> list[str]:
    merged_args = list(passthrough)
    for index in range(0, len(spec.get("default_args", [])), 2):
        option = spec["default_args"][index]
        value = spec["default_args"][index + 1]
        if not any(item == option or item.startswith(option + "=") for item in merged_args):
            merged_args.extend([option, value])
    prompt_flags = [str(item) for item in spec.get("prompt_flags", [spec["flag"]])]
    for index, value in enumerate(merged_args):
        if value in prompt_flags:
            if index + 1 >= len(merged_args):
                raise AgentError(f"Missing value after prompt option: {value}")
            merged = list(merged_args)
            merged[index + 1] = prompt + "\n\nAdditional user instruction:\n" + merged_args[index + 1]
            return merged
        for flag in prompt_flags:
            if value.startswith(flag + "="):
                option, requested = value.split("=", 1)
                merged = list(merged_args)
                merged[index] = option + "=" + prompt + "\n\nAdditional user instruction:\n" + requested
                return merged
    return [str(spec["flag"]), prompt, *merged_args]


def stdin_prompt(spec: dict[str, Any], prompt: str) -> str | None:
    """The text to write to the agent's stdin, when that is how it takes input."""
    return prompt if spec["inject"] == "stdin" else None


def suppresses(spec: dict[str, Any], line: str) -> bool:
    stripped = line.strip()
    return any(stripped.startswith(str(pattern)) or str(pattern) in stripped for pattern in spec.get("suppress", []))
