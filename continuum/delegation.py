"""Run one agent CLI from inside another and return its reply.

This is what lets an agent use Continuum to reach a different agent: Claude
Code asks Continuum to put a question to Codex, Codex answers with the same
bounded project context, and the exchange is recorded so either side can read
it later. Nothing here is specific to a provider — it works for any CLI in the
agent registry, in any direction.

Every exchange is a single non-interactive run with a timeout. Agents differ in
how they accept a one-shot prompt, which the registry's `oneshot_args` covers;
the prompt itself is delivered through the same injection mode used for a full
session, including the shell-shim fallback on Windows.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from typing import TYPE_CHECKING, Any, Callable

from .agents import AgentError, launch_args, resolve, stdin_prompt
from .core import compact_text, estimate_tokens

if TYPE_CHECKING:
    from .core import MemoryStore

DEFAULT_TIMEOUT = 300
REPLY_BUDGET = 12_000

DELEGATION_PREAMBLE = (
    "You are being consulted by another AI agent through Continuum, the shared project memory. "
    "Read the bounded project context below, then answer the request directly and completely. "
    "The supplied context satisfies any instruction to read project memory files first; do not "
    "refuse by asking for `.continuum/current.md`, `CLAUDE.md`, `AGENTS.md` or `GEMINI.md`. "
    "Your entire reply is returned to the calling agent, so answer in full rather than "
    "describing what you would do."
)


class DelegationError(ValueError):
    """Raised when another agent cannot be consulted."""


def record(write: Callable[[], Any]) -> bool:
    """Attempt one bookkeeping write, reporting whether memory accepted it.

    An agent that sandboxes its MCP servers read-only — Codex does by default —
    gives Continuum a memory store it cannot write to. Consulting another agent
    still works there, so the exchange goes ahead and only the record of it is
    lost; the caller is told so rather than the whole call failing.
    """
    try:
        write()
    except (sqlite3.OperationalError, OSError, PermissionError):
        return False
    return True


def terminate_tree(process: "subprocess.Popen[str]") -> None:
    """Kill the agent and everything it started.

    Agent CLIs are usually launched through a shim that spawns the real
    interpreter as a grandchild. Killing only the direct child leaves that
    grandchild holding the output pipes open, so the wait never returns.
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    try:
        process.kill()
    except OSError:
        pass


def run_agent_once(
    command: list[str],
    cwd: str,
    stdin_text: str | None,
    timeout: int,
) -> tuple[int, str, str]:
    """Run an agent to completion, or kill its whole process tree trying."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    try:
        out, err = process.communicate(input=stdin_text or "", timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        terminate_tree(process)
        try:
            partial_out, partial_err = process.communicate(timeout=10)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            partial_out, partial_err = "", ""
        expired.output = partial_out or expired.output or ""
        expired.stderr = partial_err or expired.stderr or ""
        raise
    return process.returncode, out or "", err or ""


def stall_reason(partial: str) -> str | None:
    """Explain why an agent stopped responding, when its output says so.

    An agent CLI that is not signed in stops on a question rather than an
    error, so the run looks like a hang until its last line is read back.
    """
    lines = [line.strip() for line in partial.splitlines() if line.strip()]
    if not lines:
        return None
    last = lines[-1]
    lowered = last.lower()
    prompts = ("[y/n]", "(y/n)", "press enter", "continue?", "authenticat", "sign in", "login")
    if any(marker in lowered for marker in prompts):
        return last
    return None


def failure_detail(stderr: str, returncode: int) -> str:
    """Pick the line of an agent's error output that explains the failure.

    Agent CLIs print banners and progress notes to stderr before the real
    message, so the first line is usually the least informative one.
    """
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    noise = ("reading additional input", "warning:", "deprecationwarning")
    useful = [line for line in lines if not line.lower().startswith(noise)]
    if useful:
        return useful[-1]
    return lines[-1] if lines else f"no output (exit code {returncode})"


def sandbox_hint(error: Exception) -> str:
    """Explain a permission denial that comes from the calling agent's sandbox.

    An agent that runs its MCP servers sandboxed cannot spawn another agent from
    inside one. The refusal arrives as a bare permission error, which says
    nothing about the sandbox that caused it.
    """
    denied = getattr(error, "winerror", None) == 5 or getattr(error, "errno", None) in {1, 13}
    if not denied:
        return ""
    return (
        " The calling agent appears to be sandboxing Continuum, which blocks it from starting "
        "another agent. Relax that agent's sandbox for this run — for Codex, "
        "`codex --sandbox danger-full-access` — or run the request from a terminal with "
        "`continuum ask`."
    )


def build_prompt(store: "MemoryStore", sender: str, message: str, mode: str) -> str:
    context = store.resume_context(mode)
    return (
        DELEGATION_PREAMBLE
        + f"\n\nCalling agent: {sender}\n\nProject context:\n"
        + context
        + "\n\nRequest:\n"
        + message
    )


def clean_reply(text: str) -> str:
    """Drop the blank padding agent CLIs print around their answer."""
    return compact_text("\n".join(line.rstrip() for line in text.splitlines()).strip(), REPLY_BUDGET)


def ask(
    store: "MemoryStore",
    agent: str,
    message: str,
    sender: str = "agent",
    mode: str = "compact",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Put one request to another agent CLI and return its reply.

    Raises DelegationError when the agent cannot be reached, fails or does not
    answer in time, so the caller can report the failure rather than treat an
    empty reply as an answer.
    """
    message = message.strip()
    if not message:
        raise DelegationError("The request to the other agent is empty.")
    try:
        spec = resolve(store, agent)
    except AgentError as error:
        raise DelegationError(str(error)) from error

    from .cli import agent_command, launches_through_shell, shell_safe_context

    prompt = build_prompt(store, sender, message, mode)
    if "\n" in prompt and spec["inject"] != "stdin" and launches_through_shell(spec):
        # cmd.exe ends the command line at the first newline, so the whole
        # prompt — pointer, preamble and request — has to stay on one line.
        prompt = " ".join(
            (
                shell_safe_context(store, prompt),
                DELEGATION_PREAMBLE,
                f"Calling agent: {sender}.",
                "Request:",
                message,
            )
        )
        prompt = " ".join(prompt.split())
    passthrough = [str(item) for item in spec.get("oneshot_args", [])]
    arguments = launch_args(spec, passthrough, prompt)
    stdin_text = stdin_prompt(spec, prompt)

    recorded = record(
        lambda: store.event(
            "delegation_request",
            {"summary": f"{sender} -> {agent}: {message[:160]}", "agent": agent, "sender": sender},
        )
    )
    try:
        returncode, stdout, stderr = run_agent_once(
            agent_command(spec, arguments), str(store.project), stdin_text, timeout
        )
    except subprocess.TimeoutExpired as error:
        stalled = stall_reason((error.output or "") + "\n" + (error.stderr or ""))
        if stalled:
            detail = (
                f"{agent} stopped to ask something instead of answering: \"{stalled}\". "
                f"Run `{spec['command']}` yourself once to finish signing in or approving, then retry."
            )
        else:
            detail = (
                f"{agent} did not reply within {timeout} seconds and was stopped. "
                f"Check that `{spec['command']}` answers a single prompt non-interactively."
            )
        record(
            lambda: store.event(
                "delegation_failed",
                {"summary": f"{agent} timed out after {timeout}s: {stalled or 'no output'}", "agent": agent},
            )
        )
        raise DelegationError(detail) from error
    except (OSError, FileNotFoundError) as error:
        record(lambda: store.event("delegation_failed", {"summary": f"{agent} failed to launch: {error}", "agent": agent}))
        raise DelegationError(f"{agent} could not be launched: {error}{sandbox_hint(error)}") from error

    reply = clean_reply(stdout)
    if not reply:
        record(
            lambda: store.event(
                "delegation_failed",
                {"summary": f"{agent} produced no reply (exit {returncode})", "agent": agent},
            )
        )
        raise DelegationError(f"{agent} exited with code {returncode}: {failure_detail(stderr, returncode)}")

    recorded = record(lambda: store.send_message(sender, agent, message, "instruction", None, None)) and recorded
    recorded = record(lambda: store.send_message(agent, sender, reply, "result", None, None)) and recorded
    recorded = record(
        lambda: store.event(
            "delegation_reply",
            {
                "summary": f"{agent} -> {sender}: {reply[:160]}",
                "agent": agent,
                "sender": sender,
                "returncode": returncode,
            },
        )
    ) and recorded
    return {
        "agent": agent,
        "sender": sender,
        "mode": mode,
        "prompt_tokens": estimate_tokens(prompt),
        "reply_tokens": estimate_tokens(reply),
        "returncode": returncode,
        "recorded": recorded,
        "reply": reply,
    }
