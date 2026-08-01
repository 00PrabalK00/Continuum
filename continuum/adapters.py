"""Provider-specific interaction profiles for Continuum-launched terminals."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


@dataclass(frozen=True)
class AdapterEvent:
    """One explicit terminal lifecycle observation."""

    kind: str
    phase: str
    summary: str

    def payload(self, agent: str, adapter: str) -> dict[str, Any]:
        return {
            "agent": agent,
            "adapter": adapter,
            "phase": self.phase,
            "summary": self.summary,
        }


@dataclass
class AdapterState:
    phase: str = "STARTING"
    approval_prompts: int = 0
    input_prompts: int = 0
    errors: int = 0
    last_event: str = ""


class AgentTerminalAdapter:
    """Base profile for launching and observing one agent CLI."""

    agent = ""
    display_name = ""
    context_strategy = ""
    ready_patterns: tuple[str, ...] = ()
    approval_patterns: tuple[str, ...] = (
        "do you want to proceed",
        "allow this action",
        "approve this action",
        "confirm permission",
    )
    prompt_patterns: tuple[str, ...] = (
        "press enter to continue",
        "select an option",
        "choose an option",
    )
    error_patterns: tuple[str, ...] = (
        "fatal error",
        "authentication failed",
        "login required",
    )

    def __init__(self, project: Path) -> None:
        self.project = project.resolve()
        self.state = AdapterState()
        self._window = ""
        self._emitted: set[str] = set()

    @property
    def name(self) -> str:
        return f"{self.agent}_interactive"

    def prepare_args(self, passthrough: list[str], context: str | None = None) -> list[str]:
        args = list(passthrough)
        return [*args, context] if context else args

    def capabilities(self) -> dict[str, str]:
        return {
            "agent": self.agent,
            "adapter": self.name,
            "terminal": "PTY/ConPTY",
            "context": self.context_strategy,
            "approval": "observes explicit prompts; never auto-approves",
            "cancel": "Ctrl+C is forwarded to the live CLI process",
        }

    def feed(self, chunk: str) -> list[AdapterEvent]:
        """Parse only explicit visible UI text; never infer hidden agent state."""
        clean = ANSI_ESCAPE.sub("", chunk).replace("\r", "\n").lower()
        self._window = (self._window + clean)[-2000:]
        events: list[AdapterEvent] = []
        events.extend(self._match(self.ready_patterns, "ready", "READY", "is ready for interaction"))
        events.extend(self._match(self.prompt_patterns, "input", "WAITING_INPUT", "requested terminal input"))
        events.extend(self._match(self.approval_patterns, "approval", "WAITING_APPROVAL", "requested approval"))
        events.extend(self._match(self.error_patterns, "error", "FAILED", "reported a terminal error"))
        if clean.strip() and self.state.phase == "STARTING":
            events.append(self._transition("output", "WORKING", "started terminal output"))
        return events

    def finish(self, returncode: int) -> AdapterEvent:
        if returncode in {130, -2}:
            return self._transition("cancelled", "CANCELLED", "was interrupted")
        if returncode:
            return self._transition("exit", "FAILED", f"exited with code {returncode}")
        return self._transition("exit", "EXITED", "exited successfully")

    def _match(self, patterns: tuple[str, ...], kind: str, phase: str, message: str) -> list[AdapterEvent]:
        for pattern in patterns:
            key = f"{kind}:{pattern}"
            if pattern in self._window and key not in self._emitted:
                self._emitted.add(key)
                if kind == "approval":
                    self.state.approval_prompts += 1
                elif kind == "input":
                    self.state.input_prompts += 1
                elif kind == "error":
                    self.state.errors += 1
                return [self._transition(kind, phase, f"{self.display_name} {message}.")]
        return []

    def _transition(self, kind: str, phase: str, summary: str) -> AdapterEvent:
        self.state.phase = phase
        self.state.last_event = summary
        return AdapterEvent(kind, phase, summary)


class ClaudeTerminalAdapter(AgentTerminalAdapter):
    agent = "claude"
    display_name = "Claude Code"
    context_strategy = "initial positional prompt"
    ready_patterns = ("claude code",)
    approval_patterns = AgentTerminalAdapter.approval_patterns + (
        "allow claude",
        "yes, allow",
    )


class CodexTerminalAdapter(AgentTerminalAdapter):
    agent = "codex"
    display_name = "Codex"
    context_strategy = "project-scoped TUI with initial positional prompt"
    ready_patterns = ("codex",)
    approval_patterns = AgentTerminalAdapter.approval_patterns + (
        "approve command",
        "allow command",
    )

    def prepare_args(self, passthrough: list[str], context: str | None = None) -> list[str]:
        args = list(passthrough)
        if "--no-alt-screen" not in args:
            args = ["--no-alt-screen", *args]
        if not self._has_project_scope(args):
            args = ["-C", str(self.project), *args]
        return [*args, context] if context else args

    @staticmethod
    def _has_project_scope(args: list[str]) -> bool:
        return any(value in {"-C", "--cd"} or value.startswith("--cd=") or value.startswith("-C=") for value in args)


class GeminiTerminalAdapter(AgentTerminalAdapter):
    agent = "gemini"
    display_name = "Gemini CLI"
    context_strategy = "--prompt-interactive context injection"
    ready_patterns = ("gemini",)
    approval_patterns = AgentTerminalAdapter.approval_patterns + (
        "approve tool",
        "allow tool",
    )

    def prepare_args(self, passthrough: list[str], context: str | None = None) -> list[str]:
        args = list(passthrough)
        if "--screen-reader" not in args:
            args = ["--screen-reader", *args]
        if not context:
            return args
        for index, value in enumerate(args):
            if value in {"-p", "--prompt", "-i", "--prompt-interactive"}:
                if index + 1 >= len(args):
                    raise ValueError(f"Missing value after Gemini prompt option: {value}")
                merged = list(args)
                merged[index + 1] = context + "\n\nAdditional user instruction:\n" + args[index + 1]
                return merged
            if value.startswith("--prompt=") or value.startswith("--prompt-interactive="):
                option, requested = value.split("=", 1)
                merged = list(args)
                merged[index] = option + "=" + context + "\n\nAdditional user instruction:\n" + requested
                return merged
        return ["--prompt-interactive", context, *args]


ADAPTERS: dict[str, type[AgentTerminalAdapter]] = {
    "claude": ClaudeTerminalAdapter,
    "codex": CodexTerminalAdapter,
    "gemini": GeminiTerminalAdapter,
}


class GenericTerminalAdapter(AgentTerminalAdapter):
    """Fallback profile for any agent CLI without a tuned adapter.

    Passes the bounded context as a trailing positional argument, which is the
    convention most command-line agents accept, and observes the shared ready,
    approval and error patterns.
    """

    display_name = "Agent CLI"
    context_strategy = "initial positional prompt"

    def __init__(self, project: Path, agent: str = "agent") -> None:
        super().__init__(project)
        self.agent = agent


def terminal_adapter(agent: str, project: Path) -> AgentTerminalAdapter:
    factory = ADAPTERS.get(agent)
    if factory is not None:
        return factory(project)
    return GenericTerminalAdapter(project, agent)


def terminal_adapter_capabilities(project: Path) -> list[dict[str, str]]:
    return [ADAPTERS[agent](project).capabilities() for agent in ("claude", "codex", "gemini")]
