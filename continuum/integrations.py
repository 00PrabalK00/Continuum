"""Install Continuum into each AI coding agent's own workflow.

Continuum is only useful if it is already there when you start working. Rather
than asking people to remember an extra command, this module detects the agents
on the machine and installs Continuum for each one in that agent's native
format: an MCP server registration where the agent speaks MCP, a rule or skill
file where it reads instructions from disk, and session hooks where it supports
them so context loads and handoffs are written without anyone typing anything.

Every installer is idempotent and reports what it did, so the whole thing is
safe to re-run and safe to preview with `--dry-run`.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .core import write_text

if TYPE_CHECKING:
    from .core import MemoryStore

SKIPPED = "skipped"
INSTALLED = "installed"
ALREADY = "already"

# What every agent is told about Continuum. Kept short: it competes with the
# user's own instructions for attention, and the tool descriptions carry detail.
AGENT_INSTRUCTIONS = """## Continuum Shared Memory

This project uses Continuum, a local shared memory across AI coding agents.

At the start of a task, read the current context before asking the user to
re-explain anything:

- `.continuum/current.md` — where the work stands
- `.continuum/latest_handoff.md` — what the previous agent left for you

If the Continuum MCP server is connected, prefer its tools over reading files:
`get_startup_context` first, then `search_memory` and `expand_memory` for
targeted detail. Do not load full history by default.

## Recording progress

Record progress with the `save_progress` tool (or `continuum save "<what you
did> | <next step>"`), so the next session or a different agent continues
instead of starting over. Record:

- whenever the user asks you to save
- when you finish something worth not losing
- when you notice you are running low on context, before you run out, not after

You are the only one who can see how much context you have left, so this is
your call to make rather than something Continuum can detect for you.

If you already know the state and the next action, pass them. If you do not,
call `save_progress` with no arguments and Continuum writes the summary from
what it has recorded. Check `get_latest_handoff` first when you are unsure
whether something is already recorded, so you are not writing a handoff after
every message.

To hand work to a different AI, use `list_agents` and `ask_agent`.
"""

RULE_HEADER = "Continuum Shared Memory"


@dataclass
class Result:
    target: str
    label: str
    status: str
    detail: str


def marker_block(body: str) -> str:
    return body if body.endswith("\n") else body + "\n"


def write_rule_file(path: Path, body: str, label: str, target: str) -> Result:
    """Write one instruction file, leaving an existing Continuum one alone."""
    if path.exists() and RULE_HEADER in path.read_text(encoding="utf-8", errors="replace"):
        return Result(target, label, ALREADY, str(path))
    write_text(path, marker_block(body))
    return Result(target, label, INSTALLED, str(path))


def append_to_memory_file(path: Path, label: str, target: str) -> Result:
    """Append the instructions to an agent's memory file, keeping what is there."""
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if RULE_HEADER in existing:
        return Result(target, label, ALREADY, str(path))
    separator = "\n\n" if existing.strip() else ""
    write_text(path, existing.rstrip("\n") + separator + marker_block(AGENT_INSTRUCTIONS))
    return Result(target, label, INSTALLED, str(path))


def claude_hooks(store: "MemoryStore") -> dict:
    """Load context at session start and record a handoff at session end.

    These are what make Continuum part of the workflow rather than a command to
    remember: the agent starts already knowing where the work stands, and the
    handoff is written whether or not anyone remembers to ask for it.
    """
    project = store.project.as_posix()
    return {
        "SessionStart": [
            {
                "matcher": "startup|resume",
                "hooks": [
                    {
                        "type": "command",
                        "command": f'continuum hook session-start --project "{project}"',
                        "timeout": 15,
                        "statusMessage": "Loading Continuum context",
                    }
                ],
            }
        ],
        "SessionEnd": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f'continuum hook session-end --project "{project}"',
                        "timeout": 60,
                        "statusMessage": "Recording Continuum handoff",
                    }
                ],
            }
        ],
    }


def merge_hooks(settings: dict, wanted: dict) -> bool:
    """Add Continuum's hooks without disturbing hooks already configured."""
    hooks = settings.get("hooks")
    hooks = hooks if isinstance(hooks, dict) else {}
    changed = False
    for event, entries in wanted.items():
        current = hooks.get(event)
        current = list(current) if isinstance(current, list) else []
        if any("continuum hook" in json.dumps(entry) for entry in current):
            continue
        current.extend(entries)
        hooks[event] = current
        changed = True
    if changed:
        settings["hooks"] = hooks
    return changed


def install_claude(store: "MemoryStore") -> list[Result]:
    from .cli import register_claude_mcp

    message = register_claude_mcp(store)
    if "skipped" in message:
        status = SKIPPED
    elif "already" in message:
        status = ALREADY
    else:
        status = INSTALLED
    results = [Result("claude", "Claude Code", status, message)]
    skill = store.project / ".claude" / "skills" / "continuum" / "SKILL.md"
    body = (
        "---\n"
        "name: continuum\n"
        "description: >-\n"
        "  Shared project memory across AI agents. Use when resuming work, when asked what\n"
        "  was happening, when running low on context, or to hand work to another AI.\n"
        "---\n\n" + AGENT_INSTRUCTIONS
    )
    results.append(write_rule_file(skill, body, "Claude Code skill", "claude"))

    settings_path = store.project / ".claude" / "settings.json"
    settings: dict = {}
    if settings_path.exists():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
            settings = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            results.append(Result("claude", "Claude Code hooks", SKIPPED, f"{settings_path} is not readable JSON"))
            return results
    if merge_hooks(settings, claude_hooks(store)):
        write_text(settings_path, json.dumps(settings, indent=2) + "\n")
        results.append(Result("claude", "Claude Code hooks", INSTALLED, str(settings_path)))
    else:
        results.append(Result("claude", "Claude Code hooks", ALREADY, str(settings_path)))
    return results


def install_codex(store: "MemoryStore") -> list[Result]:
    from .cli import register_codex_mcp

    message = register_codex_mcp(store)
    status = ALREADY if "already" in message else INSTALLED
    return [
        Result("codex", "Codex", status, message),
        append_to_memory_file(store.project / "AGENTS.md", "Codex instructions", "codex"),
    ]


def install_gemini(store: "MemoryStore") -> list[Result]:
    from .cli import register_gemini_mcp

    message = register_gemini_mcp(store)
    status = ALREADY if "already" in message else INSTALLED
    return [
        Result("gemini", "Gemini CLI", status, message),
        append_to_memory_file(store.project / "GEMINI.md", "Gemini instructions", "gemini"),
    ]


def install_cursor(store: "MemoryStore") -> list[Result]:
    body = (
        "---\n"
        'description: "Continuum shared memory — read project context before asking the user to repeat it"\n'
        "alwaysApply: true\n"
        "---\n\n" + AGENT_INSTRUCTIONS
    )
    path = store.project / ".cursor" / "rules" / "continuum.mdc"
    return [write_rule_file(path, body, "Cursor", "cursor")]


def install_windsurf(store: "MemoryStore") -> list[Result]:
    path = store.project / ".windsurf" / "rules" / "continuum.md"
    return [write_rule_file(path, AGENT_INSTRUCTIONS, "Windsurf", "windsurf")]


def install_cline(store: "MemoryStore") -> list[Result]:
    path = store.project / ".clinerules" / "continuum.md"
    return [write_rule_file(path, AGENT_INSTRUCTIONS, "Cline", "cline")]


def install_generic(store: "MemoryStore") -> list[Result]:
    """AGENTS.md is the convention most other agent CLIs already read."""
    return [append_to_memory_file(store.project / "AGENTS.md", "AGENTS.md (any other agent)", "agents-md")]


def home_has(*names: str) -> bool:
    home = Path.home()
    return any((home / name).exists() for name in names)


@dataclass
class Target:
    id: str
    label: str
    detect: Callable[[], bool]
    install: Callable[["MemoryStore"], list[Result]]


TARGETS: list[Target] = [
    Target("claude", "Claude Code", lambda: shutil.which("claude") is not None, install_claude),
    Target("codex", "Codex", lambda: shutil.which("codex") is not None, install_codex),
    Target("gemini", "Gemini CLI", lambda: shutil.which("gemini") is not None, install_gemini),
    Target(
        "cursor",
        "Cursor",
        lambda: shutil.which("cursor") is not None or home_has(".cursor"),
        install_cursor,
    ),
    Target(
        "windsurf",
        "Windsurf",
        lambda: shutil.which("windsurf") is not None or home_has(".codeium", ".windsurf"),
        install_windsurf,
    ),
    Target("cline", "Cline", lambda: home_has(".clinerules"), install_cline),
    # Always installed: any agent CLI Continuum does not know by name still
    # reads AGENTS.md, so this is what makes an unlisted agent work.
    Target("agents-md", "AGENTS.md (any other agent)", lambda: True, install_generic),
]


def detect() -> list[Target]:
    return [target for target in TARGETS if target.detect()]


def install(store: "MemoryStore", only: list[str] | None = None) -> list[Result]:
    chosen = [target for target in (detect() if not only else TARGETS) if not only or target.id in only]
    results: list[Result] = []
    for target in chosen:
        try:
            results.extend(target.install(store))
        except (OSError, ValueError) as error:
            results.append(Result(target.id, target.label, SKIPPED, str(error)))
    return results
