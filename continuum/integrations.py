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
UPDATED = "updated"

# What every agent is told about Continuum. Kept short: it competes with the
# user's own instructions for attention, and the tool descriptions carry detail.
#
# The table names the tools rather than describing them in prose. An agent
# reading an instruction file has not yet seen the MCP tool list, and an agent
# whose harness never connects the server has no tool list at all, so each row
# carries the tool and the command that does the same thing without it.
AGENT_INSTRUCTIONS = """## Continuum Shared Memory

This project uses Continuum, a local shared memory across AI coding agents.

At the start of a task, read the current context before asking the user to
re-explain anything:

- `.continuum/current.md` — where the work stands
- `.continuum/latest_handoff.md` — what the previous agent left for you

## The tools

If the Continuum MCP server is connected, prefer its tools over reading the
files. Start narrow and expand; do not load full history by default.

| What you need | MCP tool | Without MCP |
| --- | --- | --- |
| Where the work stands | `get_startup_context` | `continuum` |
| What the last agent left | `get_latest_handoff` | read `.continuum/latest_handoff.md` |
| One exact topic | `search_memory` | `continuum search "<topic>"` |
| Full text behind a result | `expand_memory` | `continuum log` |
| Record what you did | `save_progress` | `continuum save "<did> \\| <next step>"` |
| Hand the work over | `write_handoff` | `continuum handoff --task "<state>" --next-step "<next>"` |
| Reach another agent | `list_agents`, `ask_agent` | `continuum ask <agent> "<question>"` |

`get_raw_log` returns the unsummarised history. It is a last resort, not the
read path — the compact views above are what keep this cheap.

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

# The headings Continuum has ever written. A block installed by an older release
# carries no marker, so this is how its end is found: the block runs from its
# heading until a heading Continuum did not write, or to the end of the file.
OWN_HEADINGS = ("## " + RULE_HEADER, "## The tools", "## Recording progress")

BLOCK_OPEN = "<!-- continuum:instructions"
BLOCK_CLOSE = "<!-- /continuum:instructions -->"


def block_version(body: str) -> str:
    """A short digest of the text, so a rerun can tell current from outdated.

    Deriving the version from the text rather than bumping a constant means an
    edit to the instructions can never ship without changing the version, which
    is the failure that leaves every existing project on the old block.
    """
    from hashlib import sha256

    return sha256(body.strip().encode("utf-8")).hexdigest()[:12]


@dataclass
class Result:
    target: str
    label: str
    status: str
    detail: str


def marker_block(body: str) -> str:
    """Wrap the instructions so a later release can find and replace them."""
    body = body.strip("\n")
    return f"{BLOCK_OPEN} v={block_version(body)} -->\n{body}\n{BLOCK_CLOSE}\n"


def strip_block(text: str) -> tuple[str, str | None]:
    """Return the text without Continuum's block, and the version it carried.

    Handles both shapes: the marked block this version writes, and the bare
    heading an older release left behind.
    """
    start = text.find(BLOCK_OPEN)
    if start != -1:
        opener_end = text.find("-->", start)
        version = text[start:opener_end].split("v=")[-1].strip() if opener_end != -1 else None
        end = text.find(BLOCK_CLOSE, start)
        tail = text[end + len(BLOCK_CLOSE) :] if end != -1 else ""
        return text[:start] + tail, version

    start = text.find(OWN_HEADINGS[0])
    if start == -1:
        return text, None
    # An unmarked block ends where text Continuum did not write begins. Anything
    # the user added below their own heading has to survive being upgraded.
    end = len(text)
    for offset, line in scan_headings(text, start + 1):
        if line.rstrip() not in OWN_HEADINGS:
            end = offset
            break
    return text[:start] + text[end:], None


def scan_headings(text: str, from_index: int) -> list[tuple[int, str]]:
    found = []
    index = from_index
    while True:
        index = text.find("\n## ", index)
        if index == -1:
            return found
        index += 1
        line_end = text.find("\n", index)
        found.append((index, text[index : line_end if line_end != -1 else len(text)]))


def write_instructions(path: Path, body: str, label: str, target: str, front: str = "") -> Result:
    """Write Continuum's block, replacing an older one and keeping the rest.

    Rerunning `continuum install` after an upgrade has to actually deliver the
    new instructions. Treating any existing block as final is what left every
    project already using Continuum on whatever text it was installed with.

    `front` is YAML frontmatter, which several agents only read as the first
    thing in the file, so it is written ahead of the marker rather than inside
    the block.
    """
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    remainder, version = strip_block(existing)
    if version == block_version(body):
        return Result(target, label, ALREADY, str(path))
    if front and not remainder.lstrip().startswith("---"):
        remainder = front + remainder
    separator = "\n\n" if remainder.strip() else ""
    write_text(path, remainder.rstrip("\n") + separator + marker_block(body))
    return Result(target, label, INSTALLED if not existing.strip() else UPDATED, str(path))


def write_rule_file(path: Path, body: str, label: str, target: str, front: str = "") -> Result:
    return write_instructions(path, body, label, target, front)


def append_to_memory_file(path: Path, label: str, target: str) -> Result:
    """Add the instructions to an agent's memory file, keeping what is there."""
    return write_instructions(path, AGENT_INSTRUCTIONS, label, target)


def claude_hooks(store: "MemoryStore") -> dict:
    """Load context at session start and record a handoff at session end.

    These are what make Continuum part of the workflow rather than a command to
    remember: the agent starts already knowing where the work stands, and the
    handoff is written whether or not anyone remembers to ask for it.

    The project is referenced through Claude Code's own variable rather than
    written as an absolute path. `.claude/settings.json` is shared team
    configuration that Continuum merges into rather than owns, so baking one
    machine's home directory into it would either break every other clone or
    force the whole file out of version control.
    """
    project = "${CLAUDE_PROJECT_DIR}"
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
    front = (
        "---\n"
        "name: continuum\n"
        "description: >-\n"
        "  Shared project memory across AI agents. Use when resuming work, when asked what\n"
        "  was happening, when running low on context, or to hand work to another AI.\n"
        "---\n"
    )
    results.append(write_rule_file(skill, AGENT_INSTRUCTIONS, "Claude Code skill", "claude", front))

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
    front = (
        "---\n"
        'description: "Continuum shared memory — read project context before asking the user to repeat it"\n'
        "alwaysApply: true\n"
        "---\n"
    )
    path = store.project / ".cursor" / "rules" / "continuum.mdc"
    return [write_rule_file(path, AGENT_INSTRUCTIONS, "Cursor", "cursor", front)]


def install_windsurf(store: "MemoryStore") -> list[Result]:
    path = store.project / ".windsurf" / "rules" / "continuum.md"
    return [write_rule_file(path, AGENT_INSTRUCTIONS, "Windsurf", "windsurf")]


def install_cline(store: "MemoryStore") -> list[Result]:
    path = store.project / ".clinerules" / "continuum.md"
    return [write_rule_file(path, AGENT_INSTRUCTIONS, "Cline", "cline")]


def install_copilot(store: "MemoryStore") -> list[Result]:
    """Copilot reads `.github/copilot-instructions.md` on every request.

    It is the one widely-installed agent that ignores AGENTS.md, and it ships
    inside VS Code and the JetBrains IDEs rather than as a CLI, so detection
    goes through the editor rather than through PATH.
    """
    path = store.project / ".github" / "copilot-instructions.md"
    return [append_to_memory_file(path, "GitHub Copilot", "copilot")]


def install_generic(store: "MemoryStore") -> list[Result]:
    """AGENTS.md is the convention most other agent CLIs already read."""
    return [append_to_memory_file(store.project / "AGENTS.md", "AGENTS.md (any other agent)", "agents-md")]


def home_has(*names: str) -> bool:
    home = Path.home()
    return any((home / name).exists() for name in names)


def has_jetbrains() -> bool:
    """Copilot in a JetBrains IDE, on a machine that may never have run VS Code.

    JetBrains keeps its configuration in one directory per platform, so this is
    where an install shows up whichever IDE of theirs it belongs to.
    """
    return home_has(
        "JetBrains",  # the directory itself, when the home is the config root
        ".config/JetBrains",  # Linux
        "AppData/Roaming/JetBrains",  # Windows
        "Library/Application Support/JetBrains",  # macOS
    )


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
    Target(
        "copilot",
        "GitHub Copilot",
        lambda: shutil.which("code") is not None or home_has(".vscode", ".vscode-insiders") or has_jetbrains(),
        install_copilot,
    ),
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
