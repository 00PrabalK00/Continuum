"""Interactive command console for Continuum's deterministic CLI actions."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import Callable, TextIO


AGENT_COLORS = {
    "claude": "35",
    "codex": "36",
    "gemini": "34",
    "ollama": "32",
    "openrouter": "33",
}
RESET = "\033[0m"
BOLD = "1"
DIM = "2"
PASTE_START = "\x1b[200~"
PASTE_END = "\x1b[201~"
TOP_LEVEL_COMMON_COMMANDS = {
    "init",
    "daemon",
    "up",
    "down",
    "logs",
    "handoff",
    "run",
    "resume",
    "status",
    "doctor",
    "search",
    "service",
    "autostart",
    "mcp",
    "ui",
    "shell",
    "instruct",
    "chat",
}
NESTED_COMMON_COMMANDS = {
    "session",
    "adapters",
    "task",
    "providers",
    "model",
    "memory",
    "context",
    "message",
    "team",
    "worktree",
    "route",
}
ALIASES = {"sessions": "session"}
PERMISSION_CHOICES = {
    "claude": {"acceptEdits", "auto", "bypassPermissions", "default", "dontAsk", "plan"},
    "codex": {"untrusted", "on-request", "never", "on-failure"},
    "gemini": {"default", "auto_edit", "yolo", "plan"},
}
DEFAULT_PERMISSIONS = {
    "claude": "default",
    "codex": "on-request",
    "gemini": "default",
}


class InteractiveShell:
    """Small slash-command console layered over established CLI commands."""

    def __init__(
        self,
        project: Path,
        vault: Path | None,
        dispatch: Callable[[list[str]], int],
        *,
        color: str = "auto",
        animation: str = "auto",
        selected_agent: str = "codex",
        input_fn: Callable[[str], str] | None = None,
        output: TextIO | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.project = project.resolve()
        self.vault = vault.resolve() if vault else None
        self.dispatch = dispatch
        self.input_fn = input_fn or input
        self.output = output or sys.stdout
        self.sleep = sleep
        self.agent = selected_agent
        self.color_enabled = color == "always" or (color == "auto" and self.output.isatty() and not os.getenv("NO_COLOR"))
        self.animation_enabled = animation == "on" or (animation == "auto" and self.output.isatty())
        self.running = True
        self.last_paste: str | None = None
        self.permissions = dict(DEFAULT_PERMISSIONS)

    def paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}{RESET}" if self.color_enabled else text

    def write(self, text: str = "") -> None:
        print(text, file=self.output)

    def pulse(self, text: str) -> None:
        if not self.animation_enabled:
            return
        for marker in (".", "..", "..."):
            self.write(self.paint(f"{text}{marker}", DIM))
            self.sleep(0.03)

    def prompt(self) -> str:
        agent = self.paint(self.agent, AGENT_COLORS[self.agent])
        return f"continuum[{agent}]> "

    def run(self) -> int:
        self.banner()
        while self.running:
            try:
                line = self.input_fn(self.prompt()).strip()
            except (EOFError, KeyboardInterrupt):
                self.write()
                break
            if line:
                self.execute(line)
        self.write("Continuum shell closed.")
        return 0

    def banner(self) -> None:
        self.write(self.paint("Continuum Interactive Shell", BOLD))
        self.write(f"Project: {self.project}")
        self.write("Type / or /help for commands. Use /terminal or /resume-terminal for live PTY/ConPTY agent sessions.")
        self.terminals()

    def execute(self, line: str) -> int:
        line, paste_chars = self.ingest_bracketed_paste(line)
        if paste_chars:
            self.write(f"Bracketed paste received: {{{paste_chars} chars}}")
        if not line.startswith("/"):
            lowered = line.lower()
            if lowered in {"clear", "cls"}:
                return self.clear()
            if lowered in {"help", "?"}:
                return self.help()
            if lowered in {"quit", "exit"}:
                return self.quit()
            if lowered == "continuum" or lowered.startswith("continuum "):
                return self.continuum_command(line)
            if paste_chars:
                self.write("Sending pasted text to the selected agent with compact Continuum context.")
            else:
                self.write(f"Sending to {self.agent} with compact Continuum context. Use / for commands.")
            return self.dispatch(["chat", *self.common(), self.agent, "compact", line])
        try:
            parts = shlex.split(line[1:])
        except ValueError as error:
            self.write(f"Invalid input: {error}")
            return 1
        if not parts:
            return self.help()
        command, rest = parts[0].lower(), parts[1:]
        local = {
            "help": lambda _: self.help(),
            "?": lambda _: self.help(),
            "quit": lambda _: self.quit(),
            "exit": lambda _: self.quit(),
            "clear": lambda _: self.clear(),
            "terminals": lambda _: self.terminals(),
            "agent": self.select_agent,
            "permissions": self.permissions_command,
            "permission": self.permissions_command,
            "perms": self.permissions_command,
            "color": self.toggle_color,
            "motion": self.toggle_motion,
        }
        if command in local:
            return local[command](rest)
        argv = self.translate(command, rest)
        if argv == []:
            return 0
        if argv is None:
            self.write(f"Unknown command: /{command}. Run /help.")
            return 1
        self.pulse(f"Running /{command}")
        return self.dispatch(argv)

    def continuum_command(self, line: str) -> int:
        try:
            parts = shlex.split(line)
        except ValueError as error:
            self.write(f"Invalid input: {error}")
            return 1
        if len(parts) == 1:
            self.write("Usage: continuum <command> [arguments]")
            return 0
        argv = self.raw_continuum_command(parts[1], parts[2:])
        if argv == []:
            return 0
        if argv is None:
            self.write(f"Unknown command: {parts[1]}. Run /help.")
            return 1
        self.pulse(f"Running continuum {parts[1]}")
        return self.dispatch(argv)

    def ingest_bracketed_paste(self, line: str) -> tuple[str, int]:
        if PASTE_START not in line:
            return line, 0
        output: list[str] = []
        pasted: list[str] = []
        index = 0
        while index < len(line):
            start = line.find(PASTE_START, index)
            if start == -1:
                output.append(line[index:])
                break
            output.append(line[index:start])
            content_start = start + len(PASTE_START)
            end = line.find(PASTE_END, content_start)
            if end == -1:
                pasted_text = line[content_start:]
                index = len(line)
            else:
                pasted_text = line[content_start:end]
                index = end + len(PASTE_END)
            pasted.append(pasted_text)
            output.append(pasted_text)
        paste_text = "".join(pasted)
        self.last_paste = paste_text
        return "".join(output), len(paste_text)

    def common(self) -> list[str]:
        args = ["--project", str(self.project)]
        if self.vault:
            args.extend(["--vault", str(self.vault)])
        return args

    def nested(self, group: str, rest: list[str]) -> list[str] | None:
        if not rest:
            self.write(f"Usage: /{group} <subcommand> [arguments]")
            return []
        return [group, rest[0], *self.common(), *rest[1:]]

    def translate(self, command: str, rest: list[str]) -> list[str] | None:
        if command in {"init", "up", "down", "status", "doctor", "logs"}:
            return [command, *self.common(), *rest]
        if command == "search":
            if not rest:
                self.write("Usage: /search <query>")
                return []
            return ["search", *self.common(), " ".join(rest)]
        if command == "handoff":
            if rest and rest[0].startswith("--"):
                return self.raw_continuum_command(command, rest)
            if "|" not in rest:
                self.write('Usage: /handoff <current task> | <next exact step>')
                return []
            divider = rest.index("|")
            task, next_step = " ".join(rest[:divider]), " ".join(rest[divider + 1 :])
            if not task or not next_step:
                self.write('Usage: /handoff <current task> | <next exact step>')
                return []
            return ["handoff", *self.common(), "--task", task, "--next-step", next_step]
        if command in {"run", "launch"}:
            if rest and rest[0].startswith("--"):
                return self.raw_continuum_command("run", rest)
            agent, passthrough = self._agent_and_rest(rest)
            return ["run", *self.common(), agent, *passthrough]
        if command in {"terminal", "pty"}:
            agent, passthrough = self._agent_and_rest(rest)
            return ["run", *self.common(), "--interactive", agent, *self.permission_args(agent, passthrough), *passthrough]
        if command in {"resume", "continue"}:
            if rest and rest[0].startswith("--"):
                return self.raw_continuum_command("resume", rest)
            agent, passthrough = self._agent_and_rest(rest)
            mode = "compact"
            if passthrough and passthrough[0] in {"compact", "normal", "deep"}:
                mode, passthrough = passthrough[0], passthrough[1:]
            return ["resume", *self.common(), agent, mode, *passthrough]
        if command == "switch":
            return self.switch_agent(rest)
        if command in {"resume-terminal", "resume-pty"}:
            agent, passthrough = self._agent_and_rest(rest)
            mode = "compact"
            if passthrough and passthrough[0] in {"compact", "normal", "deep"}:
                mode, passthrough = passthrough[0], passthrough[1:]
            return ["resume", *self.common(), "--interactive", agent, mode, *self.permission_args(agent, passthrough), *passthrough]
        if command == "memory":
            if rest and rest[0] in {"embed", "retrieve", "refresh"}:
                return self.nested("memory", rest)
            semantic = ["--semantic"] if "--semantic" in rest else []
            query = " ".join(item for item in rest if item != "--semantic")
            if not query:
                self.write("Usage: /memory <query> [--semantic]")
                return []
            return ["memory", "retrieve", *self.common(), query, *semantic]
        if command in {"task", "providers", "model", "team", "worktree", "message", "context"}:
            return self.nested(command, rest)
        if command in {"session", "sessions"}:
            return self.nested("session", rest or ["list"])
        if command == "route":
            if rest and rest[0] == "explain":
                return self.raw_continuum_command(command, rest)
            if not rest:
                self.write("Usage: /route <request>")
                return []
            return ["route", "explain", *self.common(), " ".join(rest)]
        if command == "plan":
            if not rest:
                self.write("Usage: /plan <request>")
                return []
            return ["team", "run", *self.common(), "default_dev_team", " ".join(rest)]
        if command == "instruct":
            if rest and rest[0].startswith("--"):
                return self.raw_continuum_command(command, rest)
            return self.instruct(rest)
        if command == "chat":
            return self.chat(rest)
        if command == "service":
            return ["service", *self.common(), *(rest or ["status"])]
        if command == "ui":
            if rest:
                return self.raw_continuum_command(command, rest)
            return ["ui", *self.common(), "--open", *rest]
        if command == "mcp":
            if rest:
                return self.raw_continuum_command(command, rest)
            self.write(f"Run in a dedicated terminal: continuum mcp serve --project \"{self.project}\"")
            return []
        if command == "adapters":
            if rest:
                return self.raw_continuum_command(command, rest)
            return ["adapters", "list", *self.common()]
        return self.raw_continuum_command(command, rest)

    def raw_continuum_command(self, command: str, rest: list[str]) -> list[str] | None:
        command = ALIASES.get(command, command)
        if command in TOP_LEVEL_COMMON_COMMANDS:
            return self.inject_common([command, *rest], 1)
        if command in NESTED_COMMON_COMMANDS:
            if not rest:
                self.write(f"Usage: /{command} <subcommand> [arguments]")
                return []
            return self.inject_common([command, *rest], 2)
        return None

    def inject_common(self, argv: list[str], index: int) -> list[str]:
        if "--project" in argv or any(item.startswith("--project=") for item in argv):
            return argv
        return [*argv[:index], *self.common(), *argv[index:]]

    def instruct(self, values: list[str]) -> list[str]:
        options: dict[str, str] = {}
        goal_parts: list[str] = []
        for value in values:
            if "=" in value:
                key, item = value.split("=", 1)
                if key in {"planner", "executor", "mode", "budget", "goal", "review", "tests", "handoff", "scope"}:
                    options[key] = item
                    continue
            goal_parts.append(value)
        goal = options.get("goal") or " ".join(goal_parts).strip()
        planner = options.get("planner")
        executor = options.get("executor")
        if not planner or not executor or not goal:
            self.write('Usage: /instruct planner=claude-opus-4-1-20250805 executor=codex mode=checkpoint goal="Implement PTY receipts"')
            return []
        argv = ["instruct", *self.common(), "--planner", planner, "--executor", executor, "--goal", goal]
        for key in ("mode", "budget", "review", "tests", "handoff"):
            if key in options:
                argv.extend([f"--{key}", options[key]])
        if "scope" in options:
            for item in options["scope"].split(","):
                item = item.strip()
                if item:
                    argv.extend(["--scope", item])
        return argv

    def switch_agent(self, values: list[str]) -> list[str]:
        if not values or values[0] not in {"claude", "codex", "gemini"}:
            self.write("Usage: /switch claude|codex|gemini [compact|normal|deep] [agent args]")
            return []
        agent, passthrough = values[0], values[1:]
        mode = "compact"
        if passthrough and passthrough[0] in {"compact", "normal", "deep"}:
            mode, passthrough = passthrough[0], passthrough[1:]
        self.agent = agent
        return ["resume", *self.common(), agent, mode, *passthrough]

    def chat(self, values: list[str]) -> list[str]:
        agent, rest = self._agent_and_rest(values)
        mode = "compact"
        if rest and rest[0] in {"compact", "normal", "deep"}:
            mode, rest = rest[0], rest[1:]
        if not rest:
            self.write("Usage: /chat [claude|codex|gemini] [compact|normal|deep] <message>")
            return []
        return ["chat", *self.common(), agent, mode, " ".join(rest)]

    def _agent_and_rest(self, values: list[str]) -> tuple[str, list[str]]:
        if values and values[0] in {"claude", "codex", "gemini"}:
            return values[0], values[1:]
        return self.agent, values

    def permission_args(self, agent: str, existing: list[str]) -> list[str]:
        mode = self.permissions.get(agent)
        if not mode:
            return []
        if agent == "claude":
            return [] if self._has_option(existing, "--permission-mode") else ["--permission-mode", mode]
        if agent == "codex":
            return [] if self._has_option(existing, "--ask-for-approval", "-a") else ["--ask-for-approval", mode]
        if agent == "gemini":
            return [] if self._has_option(existing, "--approval-mode") else ["--approval-mode", mode]
        return []

    @staticmethod
    def _has_option(values: list[str], *names: str) -> bool:
        return any(value in names or any(value.startswith(name + "=") for name in names) for value in values)

    def select_agent(self, values: list[str]) -> int:
        if len(values) != 1 or values[0] not in {"claude", "codex", "gemini"}:
            self.write("Usage: /agent claude|codex|gemini")
            return 1
        self.agent = values[0]
        self.write(f"Selected agent terminal: {self.paint(self.agent, AGENT_COLORS[self.agent])}")
        return 0

    def permissions_command(self, values: list[str]) -> int:
        if not values:
            self.write("Agent permissions:")
            for agent in ("claude", "codex", "gemini"):
                marker = "*" if agent == self.agent else " "
                self.write(f" {marker} {agent}: {self.permissions[agent]}")
            self.write("Usage: /permissions [claude|codex|gemini] <mode>")
            return 0
        agent, rest = self._agent_and_rest(values)
        if not rest:
            choices = ", ".join(sorted(PERMISSION_CHOICES[agent]))
            self.write(f"{agent}: {self.permissions[agent]} ({choices})")
            return 0
        mode = rest[0]
        if mode not in PERMISSION_CHOICES[agent]:
            choices = ", ".join(sorted(PERMISSION_CHOICES[agent]))
            self.write(f"Invalid {agent} permission mode: {mode}. Choices: {choices}")
            return 1
        self.permissions[agent] = mode
        self.write(f"{agent} permission mode: {mode}")
        return 0

    def toggle_color(self, values: list[str]) -> int:
        if len(values) != 1 or values[0] not in {"on", "off", "auto"}:
            self.write("Usage: /color on|off|auto")
            return 1
        self.color_enabled = values[0] == "on" or (
            values[0] == "auto" and self.output.isatty() and not os.getenv("NO_COLOR")
        )
        self.write(f"Color: {values[0]}")
        return 0

    def toggle_motion(self, values: list[str]) -> int:
        if len(values) != 1 or values[0] not in {"on", "off", "auto"}:
            self.write("Usage: /motion on|off|auto")
            return 1
        self.animation_enabled = values[0] == "on" or (values[0] == "auto" and self.output.isatty())
        self.write(f"Motion: {values[0]}")
        return 0

    def terminals(self) -> int:
        self.write("Agent terminals:")
        for name in ("claude", "codex", "gemini"):
            installed = "available" if shutil.which(name) else "not found"
            marker = "*" if name == self.agent else " "
            self.write(f" {marker} {self.paint(name, AGENT_COLORS[name])}: {installed}")
        self.write(f"   {self.paint('ollama', AGENT_COLORS['ollama'])}: model/embedding provider")
        self.write(f"   {self.paint('openrouter', AGENT_COLORS['openrouter'])}: hosted model provider")
        return 0

    def clear(self) -> int:
        self.write("\033[2J\033[H" if self.color_enabled else "")
        self.banner()
        return 0

    def quit(self) -> int:
        self.running = False
        return 0

    def help(self) -> int:
        self.write(
            """Slash commands:
  /                            Show this command list.
  /status [--events]            Show daemon, memory, task and provider state.
  /doctor                       Run deterministic health checks.
  /up | /down | /logs           Control or inspect the memory daemon.
  /handoff task | next step     Record a continuation checkpoint.
  /agent claude|codex|gemini   Choose the default agent terminal color/target.
  /permissions [agent] [mode]  Show or set approval mode for live sessions.
  /run [agent] [args]           Launch a captured-output agent session.
  /terminal [agent] [args]      Launch a live PTY/ConPTY agent terminal.
  /resume [agent] [mode]       Inject context and continue (compact/normal/deep).
  /switch agent [mode]          Select an agent and resume with context injected.
  /chat [agent] [mode] message  Send one message to an agent with context.
  /resume-terminal [agent]     Inject context into a live terminal session.
  /search words                 Exact local memory search.
  /memory words [--semantic]   Retrieve bounded memory for a query.
  /providers <subcommand>      Configure or test provider backends.
  /model <subcommand>          Call a text-only model provider.
  /team <subcommand>           Create, inspect or run a team workflow.
  /plan request                 Plan with default_dev_team.
  /instruct planner=claude-opus-4-1-20250805 executor=codex goal="..."  Create graph-backed delegation packet.
  /task <subcommand>           Manage controlled tasks and file claims.
  /session <subcommand>        Detect or bridge externally launched agents.
  /worktree <subcommand>       Manage task-isolated Git worktrees.
  /route request               Explain selected routing for a request.
  /ui                           Open Control Center (occupies this shell).
  /mcp                          Print the MCP server launch command.
  /terminals                    Show color-coded terminal/provider options.
  /adapters                     Show interactive Claude/Codex/Gemini adapter behavior.
  /color on|off|auto           Toggle ANSI color for this shell.
  /motion on|off|auto          Toggle short action animations for this shell.
  /clear                       Redraw the shell.
  /quit                        Exit.

Any current Continuum CLI command can also be entered as a slash command; the
shell injects the active project unless `--project` is already supplied.
Plain text is sent to the selected agent as `/chat <selected> compact ...`.
Bracketed paste input is stored in full and shown as a compact `{n chars}`
receipt before the command runs.

This shell controls existing Continuum actions. Full live terminal capture
requires a session launched through Continuum. Existing sessions can be
bridged cooperatively with `continuum session attach` and bounded context."""
        )
        return 0
