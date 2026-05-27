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
        self.write("Type /help for commands. Use /terminal or /resume-terminal for live PTY/ConPTY agent sessions.")
        self.terminals()

    def execute(self, line: str) -> int:
        if not line.startswith("/"):
            self.write("Use slash commands in this shell. Run /help for available actions.")
            return 1
        try:
            parts = shlex.split(line[1:])
        except ValueError as error:
            self.write(f"Invalid input: {error}")
            return 1
        if not parts:
            return 0
        command, rest = parts[0].lower(), parts[1:]
        local = {
            "help": lambda _: self.help(),
            "?": lambda _: self.help(),
            "quit": lambda _: self.quit(),
            "exit": lambda _: self.quit(),
            "clear": lambda _: self.clear(),
            "terminals": lambda _: self.terminals(),
            "agent": self.select_agent,
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
            agent, passthrough = self._agent_and_rest(rest)
            return ["run", *self.common(), agent, *passthrough]
        if command in {"terminal", "pty"}:
            agent, passthrough = self._agent_and_rest(rest)
            return ["run", *self.common(), "--interactive", agent, *passthrough]
        if command in {"resume", "continue"}:
            agent, passthrough = self._agent_and_rest(rest)
            mode = "compact"
            if passthrough and passthrough[0] in {"compact", "normal", "deep"}:
                mode, passthrough = passthrough[0], passthrough[1:]
            return ["resume", *self.common(), agent, mode, *passthrough]
        if command in {"resume-terminal", "resume-pty"}:
            agent, passthrough = self._agent_and_rest(rest)
            mode = "compact"
            if passthrough and passthrough[0] in {"compact", "normal", "deep"}:
                mode, passthrough = passthrough[0], passthrough[1:]
            return ["resume", *self.common(), "--interactive", agent, mode, *passthrough]
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
        if command == "route":
            if not rest:
                self.write("Usage: /route <request>")
                return []
            return ["route", "explain", *self.common(), " ".join(rest)]
        if command == "plan":
            if not rest:
                self.write("Usage: /plan <request>")
                return []
            return ["team", "run", *self.common(), "default_dev_team", " ".join(rest)]
        if command == "service":
            return ["service", *self.common(), *(rest or ["status"])]
        if command == "ui":
            return ["ui", *self.common(), "--open", *rest]
        if command == "mcp":
            self.write(f"Run in a dedicated terminal: continuum mcp serve --project \"{self.project}\"")
            return []
        return None

    def _agent_and_rest(self, values: list[str]) -> tuple[str, list[str]]:
        if values and values[0] in {"claude", "codex", "gemini"}:
            return values[0], values[1:]
        return self.agent, values

    def select_agent(self, values: list[str]) -> int:
        if len(values) != 1 or values[0] not in {"claude", "codex", "gemini"}:
            self.write("Usage: /agent claude|codex|gemini")
            return 1
        self.agent = values[0]
        self.write(f"Selected agent terminal: {self.paint(self.agent, AGENT_COLORS[self.agent])}")
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
  /status [--events]            Show daemon, memory, task and provider state.
  /doctor                       Run deterministic health checks.
  /up | /down | /logs           Control or inspect the memory daemon.
  /handoff task | next step     Record a continuation checkpoint.
  /agent claude|codex|gemini   Choose the default agent terminal color/target.
  /run [agent] [args]           Launch a captured-output agent session.
  /terminal [agent] [args]      Launch a live PTY/ConPTY agent terminal.
  /resume [agent] [mode]       Inject context and continue (compact/normal/deep).
  /resume-terminal [agent]     Inject context into a live terminal session.
  /search words                 Exact local memory search.
  /memory words [--semantic]   Retrieve bounded memory for a query.
  /providers <subcommand>      Configure or test provider backends.
  /model <subcommand>          Call a text-only model provider.
  /team <subcommand>           Create, inspect or run a team workflow.
  /plan request                 Plan with default_dev_team.
  /task <subcommand>           Manage controlled tasks and file claims.
  /worktree <subcommand>       Manage task-isolated Git worktrees.
  /route request               Explain selected routing for a request.
  /ui                           Open Control Center (occupies this shell).
  /mcp                          Print the MCP server launch command.
  /terminals                    Show color-coded terminal/provider options.
  /color on|off|auto           Toggle ANSI color for this shell.
  /motion on|off|auto          Toggle short action animations for this shell.
  /clear                       Redraw the shell.
  /quit                        Exit.

This shell controls existing Continuum actions. Live terminal sessions must
be launched through Continuum; attaching to externally launched sessions is
not supported."""
        )
        return 0
