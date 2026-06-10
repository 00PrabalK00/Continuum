"""Command line interface for Continuum."""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .core import (
    DEFAULT_CONTEXT_LIMIT,
    DEFAULT_THRESHOLD,
    MAX_SESSION_EXCERPT_LINES,
    MemoryStore,
    TASK_STATUSES,
    estimate_tokens,
    utc_now,
    write_text,
)
from .mcp_server import serve_stdio
from .objective import AGENT_PROVIDERS, ObjectiveError, plan_objective
from .orchestration import OrchestrationError, Orchestrator
from .policy import PolicyError, requires_approval, write_starter_policy
from . import command_risk
from . import mcp_trust
from . import audit_export
from .benchmark import capture_task, compare_captures, load_capture, render_comparison, write_capture
from .secrets_scan import scan_text
from .roi import render_roi, roi_summary
from .providers import DEFAULT_PROVIDERS, ProviderError, ProviderManager
from .services import ServiceError, ServiceManager
from .teams import PRESETS, TeamError, TeamManager
from .worktrees import WorktreeError, WorktreeManager
from .control_center import serve_control_center
from .diagnostics import project_status, run_doctor
from .context_intel import (
    diff_intel,
    gather_context_intel,
    render_diff,
    render_intel,
    render_score,
    score_intel,
)
from .evidence import EvidenceError, gather_evidence, render_packet
from .handoff_llm import generate_handoff, read_handoff_model, write_handoff_model
from .flight import FlightRecordError, gather_flight_record, render_flight_record
from .external_sessions import ExternalSessionError, ExternalSessionManager
from .terminal import TerminalUnavailable, run_terminal_process, terminal_backend
from .adapters import terminal_adapter, terminal_adapter_capabilities

AGENTS = ("claude", "gemini", "codex")


def store_from(args: argparse.Namespace) -> MemoryStore:
    return MemoryStore(Path(args.project), Path(args.vault) if getattr(args, "vault", None) else None)


def initialize(args: argparse.Namespace) -> int:
    store = store_from(args)
    store.initialize(args.context_limit, args.threshold)
    ProviderManager(store.state_dir).ensure_config()
    print(f"Initialized: {store.project}")
    print(f"Local memory: {store.state_dir}")
    if store.notes_dir:
        print(f"Obsidian notes: {store.notes_dir}")
    print("Next: run `continuum team init default_dev_team` or define your own team JSON.")
    print("Then run `continuum doctor` and `continuum up`.")
    return 0


def handoff(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)
    store.event("handoff", {"task": args.task, "next_step": args.next_step})
    store.write_handoff(args.task, args.next_step)
    print(f"Wrote: {store.state_dir / 'latest_handoff.md'}")
    if store.notes_dir:
        print(f"Mirrored: {store.notes_dir / 'Latest Handoff.md'}")
    return 0


def copy_to_clipboard(text: str) -> bool:
    """Copy text to the system clipboard. Returns False when no clipboard tool exists."""
    import tempfile

    if os.name == "nt":
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False)
        try:
            handle.write(text)
            handle.close()
            command = [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Get-Content -Raw -Encoding UTF8 '{handle.name}' | Set-Clipboard",
            ]
            completed = subprocess.run(command, capture_output=True, check=False)
            return completed.returncode == 0
        except OSError:
            return False
        finally:
            Path(handle.name).unlink(missing_ok=True)
    if sys.platform == "darwin":
        command = ["pbcopy"]
    else:
        for candidate in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            if shutil.which(candidate[0]):
                command = candidate
                break
        else:
            return False
    try:
        completed = subprocess.run(command, input=text.encode("utf-8"), capture_output=True, check=False)
    except OSError:
        return False
    return completed.returncode == 0


def format_age(timestamp: float) -> str:
    seconds = max(0, time.time() - timestamp)
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} minutes ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(seconds // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


def checkpoint_handoff(store: MemoryStore, session_tail: list[str], fallback: tuple[str, str]) -> None:
    """Write the context-limit checkpoint handoff, via the handoff LLM when configured."""
    generated = None
    try:
        generated = generate_handoff(store, session_tail=session_tail)
    except ProviderError as error:
        print(f"\n[Handoff LLM unavailable ({error}); wrote a plain checkpoint handoff.]", file=sys.stderr)
    store.write_handoff(*(generated or fallback))


def quick_save(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)
    text = " ".join(args.text).strip()
    generated_by = None
    if text:
        task, _, next_step = text.partition("|")
        task = task.strip()
        next_step = next_step.strip() or None
    else:
        generated = None
        try:
            generated = generate_handoff(store)
        except ProviderError as error:
            print(f"Handoff LLM unavailable ({error}); using recorded state instead.", file=sys.stderr)
        if generated:
            task, next_step = generated
            selected = read_handoff_model(store)
            generated_by = selected["provider"] + (f":{selected['model']}" if selected["model"] else "")
        else:
            latest = store.latest_task()
            if latest is None:
                raise SystemExit(
                    'Nothing to save yet. Describe what you were doing, for example:\n'
                    '  continuum save "fixed the auth bug | next: test the retry logic"'
                )
            task, next_step = latest
    store.event("handoff", {"task": task, "next_step": next_step})
    store.write_handoff(task, next_step)
    print(f"Saved: {task}")
    if next_step:
        print(f"Next:  {next_step}")
    if generated_by:
        print(f"Summarized by handoff LLM: {generated_by}")
    print("Resume anywhere with `continuum go claude|codex|gemini` or `continuum copy`.")
    return 0


def handoff_llm_set(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)
    manager = ProviderManager(store.state_dir, store)
    providers = manager.read().get("providers", {})
    if args.provider not in providers:
        model_names = sorted(name for name, entry in providers.items() if entry.get("kind") == "model")
        raise SystemExit(f"Unknown provider: {args.provider}. Choose from: {', '.join(model_names)}.")
    if providers[args.provider].get("kind") != "model":
        raise SystemExit(
            f"{args.provider} is an agent CLI. The handoff LLM must be a model provider (ollama, openrouter)."
        )
    if not providers[args.provider].get("enabled"):
        manager.add(args.provider)
        print(f"Enabled provider: {args.provider}")
    write_handoff_model(store, args.provider, args.model)
    label = args.provider + (f" ({args.model})" if args.model else " (provider default model)")
    print(f"Handoff LLM set: {label}")
    print("Used for: bare `continuum save` summaries and context-limit checkpoint handoffs.")
    print(f"Verify connectivity: continuum providers test {args.provider}")
    return 0


def handoff_llm_show(args: argparse.Namespace) -> int:
    store = store_from(args)
    selected = read_handoff_model(store) if store.config_file.exists() else None
    if not selected:
        print("No handoff LLM configured.")
        print("Set one: continuum handoff-llm set ollama")
        print("     or: continuum handoff-llm set openrouter openai/gpt-4o-mini")
        return 0
    print(f"Provider: {selected['provider']}")
    print(f"Model: {selected['model'] or '(provider default)'}")
    print("Used for: bare `continuum save` summaries and context-limit checkpoint handoffs.")
    return 0


def handoff_llm_off(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not store.config_file.exists() or read_handoff_model(store) is None:
        print("No handoff LLM configured.")
        return 0
    write_handoff_model(store, None)
    print("Handoff LLM disabled. Saves and checkpoints use recorded state only.")
    return 0


def copy_context(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)
    context = store.resume_context(args.mode).strip()
    if not context:
        raise SystemExit('No saved context yet. Run `continuum save "<what you were doing>"` first.')
    prompt = (
        "Context from my previous AI session, recorded by Continuum. "
        "Read it and continue helping me from exactly where it left off.\n\n" + context
    )
    print(prompt)
    if copy_to_clipboard(prompt):
        print(
            f"\n[Copied to clipboard - about {estimate_tokens(prompt)} tokens. Paste it into any AI chat.]",
            file=sys.stderr,
        )
    else:
        print("\n[No clipboard tool found — copy the text above manually.]", file=sys.stderr)
    return 0


def go(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)
    if not (store.state_dir / "latest_handoff.md").exists():
        task = store.latest_task() or (
            "New session in this project; no prior context recorded.",
            "Continue from the user's next message.",
        )
        store.write_handoff(*task)
    args.agent_args = []
    args.context_limit = None
    args.threshold = None
    args.interactive = (
        not args.no_interactive and sys.stdin.isatty() and sys.stdout.isatty()
    )
    return resume(args)


def setup(args: argparse.Namespace) -> int:
    store = store_from(args)
    store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)
    ProviderManager(store.state_dir).ensure_config()
    print(f"Initialized: {store.project}")
    found = [agent for agent in AGENTS if shutil.which(agent)]
    missing = [agent for agent in AGENTS if agent not in found]
    print(f"Agent CLIs found: {', '.join(found) or 'none'}")
    if missing:
        print(f"Not installed: {', '.join(missing)}")
    if "claude" in found:
        try:
            command = agent_command(
                "claude",
                ["mcp", "add", "continuum", "--", "continuum", "mcp", "serve", "--project", str(store.project)],
            )
            completed = subprocess.run(command, capture_output=True, text=True, cwd=str(store.project), check=False)
            output = (completed.stdout + completed.stderr).strip()
            if completed.returncode == 0:
                print("Claude Code: Continuum MCP server registered.")
            elif "already exists" in output.lower():
                print("Claude Code: Continuum MCP server already registered.")
            else:
                print(f"Claude Code MCP registration skipped: {output.splitlines()[0] if output else 'unknown error'}")
        except (OSError, FileNotFoundError) as error:
            print(f"Claude Code MCP registration skipped: {error}")
    if "codex" in found:
        print("Codex: add to .codex/config.toml ->")
        print('  [mcpServers.continuum]')
        print('  command = "continuum"')
        print(f'  args = ["mcp", "serve", "--project", "{store.project.as_posix()}"]')
    if "gemini" in found:
        print("Gemini: add to .gemini/settings.json ->")
        print(
            '  { "mcpServers": { "continuum": { "command": "continuum",'
            f' "args": ["mcp", "serve", "--project", "{store.project.as_posix()}"] }} }} }}'
        )
    print()
    print("Daily commands:")
    print('  continuum save "did X | next do Y"   save context before switching AI')
    print("  continuum go claude|codex|gemini      open the next AI with that context")
    print("  continuum copy                        copy context for any AI chat (web included)")
    return 0


def quick_status(args: argparse.Namespace) -> int:
    store = store_from(args)
    project_name = store.project.name or str(store.project)
    if not store.config_file.exists():
        print(f"Continuum - {project_name} (not initialized)")
        print('Start: continuum save "<what you are working on>"   (auto-initializes)')
        print("Agent CLI setup: continuum setup")
        return 0
    print(f"Continuum - {project_name}")
    latest = store.latest_task()
    if latest:
        print(f"Task: {latest[0]}")
        if latest[1]:
            print(f"Next: {latest[1]}")
    else:
        print("Task: nothing recorded yet")
    handoff_file = store.state_dir / "latest_handoff.md"
    if handoff_file.exists():
        print(f"Saved: {format_age(handoff_file.stat().st_mtime)}")
    print()
    print('Save context:   continuum save "did X | next do Y"')
    print("Hand to agent:  continuum go claude|codex|gemini")
    print("Paste anywhere: continuum copy")
    return 0


def daemon(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)
    store.event("daemon_started", {"summary": f"Watching {store.project}"})
    print(f"Continuum watching {store.project}. Press Ctrl+C to stop.")
    external = ExternalSessionManager(store)
    external_warning_printed = False
    try:
        while True:
            try:
                for session in external.auto_register():
                    print(f"{utc_now()}: attached external {session['agent']} session {session['session_id']} (PID {session['pid']})")
            except ExternalSessionError as error:
                if not external_warning_printed:
                    print(f"{utc_now()}: external agent detection disabled ({error})")
                    external_warning_printed = True
            changes = store.poll_changes()
            if changes:
                task = store.latest_task() or (
                    "File changes observed; no explicit active task recorded.",
                    "Write a handoff describing the active task.",
                )
                store.write_handoff(*task)
                print(f"{utc_now()}: recorded {len(changes)} changed path(s)")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        store.event("daemon_stopped", {"summary": "Stopped by user."})
        return 0


def pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_for_process_exit(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while pid_is_running(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not pid_is_running(pid)


def up(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)
    pid_path = store.state_dir / "daemon.pid"
    if pid_path.exists():
        prior_pid = int(pid_path.read_text(encoding="utf-8").strip())
        if pid_is_running(prior_pid):
            print(f"Continuum is already running (PID {prior_pid}).")
            return 0
        pid_path.unlink()
    logs = store.state_dir / "daemon_logs"
    logs.mkdir(exist_ok=True)
    stdout_path = logs / "daemon.stdout.log"
    stderr_path = logs / "daemon.stderr.log"
    command = [sys.executable, "-m", "continuum", "daemon", "--project", str(store.project)]
    if store.vault_dir:
        command += ["--vault", str(store.vault_dir)]
    child_env = os.environ.copy()
    module_root = str(Path(__file__).resolve().parents[1])
    child_env["PYTHONPATH"] = (
        module_root + os.pathsep + child_env["PYTHONPATH"] if child_env.get("PYTHONPATH") else module_root
    )
    popen_kwargs: dict[str, object] = {"cwd": str(store.project), "env": child_env}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        popen_kwargs["start_new_session"] = True
    with stdout_path.open("a", encoding="utf-8") as stdout, stderr_path.open("a", encoding="utf-8") as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr, **popen_kwargs)
    write_text(pid_path, f"{process.pid}\n")
    process.returncode = 0  # Detached child lifecycle is managed through daemon.pid.
    print(f"Continuum started (PID {process.pid}).")
    print(f"Logs: {stdout_path}")
    return 0


def down(args: argparse.Namespace) -> int:
    store = store_from(args)
    pid_path = store.state_dir / "daemon.pid"
    if not pid_path.exists():
        print("Continuum is not running for this project.")
        return 0
    pid = int(pid_path.read_text(encoding="utf-8").strip())
    if pid_is_running(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
            if not wait_for_process_exit(pid):
                action = f"Run `taskkill /PID {pid} /T /F`, then retry `continuum down`."
                print(f"Continuum did not stop within 5 seconds (PID {pid}). Next action: {action}")
                return 1
            # Allow redirected log handles to be released before callers delete temp projects.
            time.sleep(0.05)
        else:
            os.kill(pid, signal.SIGTERM)
        print(f"Stopped Continuum (PID {pid}).")
    else:
        print(f"Removed stale daemon PID {pid}.")
    pid_path.unlink(missing_ok=True)
    return 0


def logs(args: argparse.Namespace) -> int:
    path = store_from(args).state_dir / "daemon_logs" / "daemon.stdout.log"
    if not path.exists():
        print("No daemon output log exists for this project.")
        return 0
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    print("\n".join(lines[-args.tail:]))
    return 0


def agent_command(agent: str, passthrough: list[str]) -> list[str]:
    executable = shutil.which(agent)
    if not executable:
        raise FileNotFoundError(f"Agent CLI is not installed or not in PATH: {agent}")
    if executable.lower().endswith(".ps1"):
        return ["powershell", "-ExecutionPolicy", "Bypass", "-File", executable, *passthrough]
    return [executable, *passthrough]


def injected_resume_args(agent: str, passthrough: list[str], prompt: str) -> list[str]:
    if agent == "gemini":
        merged_args = list(passthrough)
        if not any(value in {"--approval-mode"} or value.startswith("--approval-mode=") for value in merged_args):
            merged_args.extend(["--approval-mode", "plan"])
        if not any(value in {"-o", "--output-format"} or value.startswith("--output-format=") for value in merged_args):
            merged_args.extend(["--output-format", "text"])
        for index, value in enumerate(merged_args):
            if value in {"-p", "--prompt", "-i", "--prompt-interactive"}:
                if index + 1 >= len(merged_args):
                    raise ValueError(f"Missing value after Gemini prompt option: {value}")
                merged = list(merged_args)
                merged[index + 1] = prompt + "\n\nAdditional user instruction:\n" + merged_args[index + 1]
                return merged
            if value.startswith("--prompt=") or value.startswith("--prompt-interactive="):
                option, requested = value.split("=", 1)
                merged = list(merged_args)
                merged[index] = option + "=" + prompt + "\n\nAdditional user instruction:\n" + requested
                return merged
        return ["--prompt", prompt, *merged_args]
    if agent == "codex":
        if passthrough and passthrough[0] == "exec":
            return [*passthrough, prompt]
        return ["exec", *passthrough, prompt]
    return [*passthrough, prompt]


def suppress_agent_display_line(agent: str, line: str) -> bool:
    if agent != "gemini":
        return False
    stripped = line.strip()
    return (
        stripped.startswith("Warning: 256-color support not detected.")
        or stripped.startswith("Ripgrep is not available.")
        or ("[DEP0190] DeprecationWarning" in stripped)
        or stripped.startswith("(Use `node --trace-deprecation")
    )


def run_agent(args: argparse.Namespace, resumed: bool = False, injected_context: str | None = None) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(
            args.context_limit or DEFAULT_CONTEXT_LIMIT,
            args.threshold if args.threshold is not None else DEFAULT_THRESHOLD,
        )
    config = store.read_config()
    limit = args.context_limit or int(config.get("context_limit", DEFAULT_CONTEXT_LIMIT))
    threshold = args.threshold if args.threshold is not None else float(
        config.get("checkpoint_threshold", DEFAULT_THRESHOLD)
    )
    agent_args = list(args.agent_args)
    if agent_args and agent_args[0] == "--":
        agent_args.pop(0)
    if injected_context:
        agent_args = injected_resume_args(args.agent, agent_args, injected_context)
    session_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{args.agent}"
    log_file = store.state_dir / "session_logs" / f"{session_id}.log"
    action = "resume" if resumed else "run"
    store.event("agent_start", {"summary": f"{action} {args.agent}", "session": session_id})
    if resumed:
        print(f"Resume context: {store.state_dir / 'latest_handoff.md'}")
        print("Bounded Continuum context injected as the agent's initial prompt.")
    print(f"Recording output: {log_file}")
    tokens = 0
    triggered = False
    tail: list[str] = []
    try:
        command = agent_command(args.agent, agent_args)
        cwd = Path(getattr(args, "cwd", store.project))
        with log_file.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdin=None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            )
            assert process.stdout is not None
            for line in process.stdout:
                if not suppress_agent_display_line(args.agent, line):
                    print(line, end="")
                output.write(line)
                output.flush()
                tokens += estimate_tokens(line)
                tail.append(line)
                tail = tail[-MAX_SESSION_EXCERPT_LINES:]
                if not triggered and tokens >= int(limit * threshold):
                    triggered = True
                    store.event("context_checkpoint", {"summary": f"Estimated tokens: {tokens}"})
                    checkpoint_handoff(
                        store,
                        tail,
                        (
                            f"`{args.agent}` reached its estimated context checkpoint.",
                            f"Resume with another agent and inspect `{log_file.name}` if needed.",
                        ),
                    )
            returncode = process.wait()
    except OSError as error:
        store.event("error", {"summary": str(error), "returncode": 1})
        store.write_handoff("Agent failed to launch.", "Fix the launch error and rerun the agent.")
        print(str(error), file=sys.stderr)
        return 1
    usage = {
        "session": session_id,
        "agent": args.agent,
        "estimated_tokens": tokens,
        "checkpoint_triggered": triggered,
        "returncode": returncode,
    }
    write_text(store.state_dir / "token_usage.json", json.dumps(usage, indent=2) + "\n")
    store.event("agent_exit", {"summary": f"{args.agent} exited with {returncode}", "returncode": returncode})
    store.write_session_note(session_id, args.agent, returncode, tokens, tail)
    task = store.latest_task() or (
        f"Wrapped `{args.agent}` session `{session_id}` completed.",
        "Review the output and record the next action.",
    )
    store.write_handoff(*task)
    return returncode


def run_interactive_agent(args: argparse.Namespace, resumed: bool = False, injected_context: str | None = None) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(
            args.context_limit or DEFAULT_CONTEXT_LIMIT,
            args.threshold if args.threshold is not None else DEFAULT_THRESHOLD,
        )
    config = store.read_config()
    limit = args.context_limit or int(config.get("context_limit", DEFAULT_CONTEXT_LIMIT))
    threshold = args.threshold if args.threshold is not None else float(
        config.get("checkpoint_threshold", DEFAULT_THRESHOLD)
    )
    agent_args = list(args.agent_args)
    if agent_args and agent_args[0] == "--":
        agent_args.pop(0)
    adapter = terminal_adapter(args.agent, store.project)
    agent_args = adapter.prepare_args(agent_args, injected_context)
    session_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{args.agent}-terminal"
    log_file = store.state_dir / "session_logs" / f"{session_id}.log"
    action = "resume" if resumed else "run"
    backend = terminal_backend()
    store.event(
        "agent_start",
        {
            "summary": f"{action} {args.agent} interactive terminal",
            "session": session_id,
            "terminal": backend,
            "adapter": adapter.name,
        },
    )
    if resumed:
        print(f"Resume context: {store.state_dir / 'latest_handoff.md'}")
        print("Bounded Continuum context injected as the agent's initial prompt.")
    print(f"Interactive terminal backend: {backend}")
    print(f"Interactive adapter: {adapter.name}")
    print(f"Recording output: {log_file}")
    tokens = 0
    triggered = False
    tail: list[str] = []
    try:
        command = agent_command(args.agent, agent_args)
        with log_file.open("w", encoding="utf-8") as output:
            def capture(chunk: str) -> None:
                nonlocal tokens, triggered, tail
                print(chunk, end="", flush=True)
                output.write(chunk)
                output.flush()
                tokens += estimate_tokens(chunk)
                tail.extend(chunk.splitlines(keepends=True))
                tail = tail[-MAX_SESSION_EXCERPT_LINES:]
                for event in adapter.feed(chunk):
                    store.event("terminal_adapter_status", event.payload(args.agent, adapter.name))
                if not triggered and tokens >= int(limit * threshold):
                    triggered = True
                    store.event("context_checkpoint", {"summary": f"Estimated tokens: {tokens}"})
                    checkpoint_handoff(
                        store,
                        tail,
                        (
                            f"`{args.agent}` reached its estimated context checkpoint in an interactive terminal.",
                            f"Resume with another agent and inspect `{log_file.name}` if needed.",
                        ),
                    )

            returncode = run_terminal_process(
                command,
                cwd=Path(getattr(args, "cwd", store.project)),
                env=os.environ.copy(),
                on_output=capture,
                input_stream=sys.stdin,
            )
    except (OSError, TerminalUnavailable) as error:
        store.event("error", {"summary": str(error), "returncode": 1})
        store.write_handoff("Interactive agent terminal failed to launch.", "Apply the reported fix and rerun the agent.")
        print(str(error), file=sys.stderr)
        return 1
    usage = {
        "session": session_id,
        "agent": args.agent,
        "terminal": backend,
        "adapter": adapter.name,
        "adapter_phase": adapter.state.phase,
        "approval_prompts": adapter.state.approval_prompts,
        "input_prompts": adapter.state.input_prompts,
        "estimated_tokens": tokens,
        "checkpoint_triggered": triggered,
        "returncode": returncode,
    }
    completed = adapter.finish(returncode)
    usage["adapter_phase"] = adapter.state.phase
    write_text(store.state_dir / "token_usage.json", json.dumps(usage, indent=2) + "\n")
    store.event("terminal_adapter_status", completed.payload(args.agent, adapter.name))
    store.event(
        "agent_exit",
        {"summary": f"{args.agent} interactive terminal exited with {returncode}", "returncode": returncode},
    )
    store.write_session_note(session_id, args.agent, returncode, tokens, tail)
    task = store.latest_task() or (
        f"Interactive `{args.agent}` session `{session_id}` completed.",
        "Review the terminal output and record the next action.",
    )
    store.write_handoff(*task)
    return returncode


def adapters_list(args: argparse.Namespace) -> int:
    for adapter in terminal_adapter_capabilities(Path(args.project)):
        print(f"{adapter['agent']}: {adapter['adapter']}")
        print(f"  Terminal: {adapter['terminal']}")
        print(f"  Context: {adapter['context']}")
        print(f"  Approval: {adapter['approval']}")
        print(f"  Cancellation: {adapter['cancel']}")
    return 0


def run(args: argparse.Namespace) -> int:
    return run_interactive_agent(args) if args.interactive else run_agent(args)


def resume(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not (store.state_dir / "latest_handoff.md").exists():
        raise SystemExit("No handoff exists yet. Run `continuum init` or `continuum handoff` first.")
    context = store.resume_context(args.mode)
    print(f"Resume mode: {args.mode}")
    print(f"Estimated context: {estimate_tokens(context)} tokens")
    print("Included: compact project state and the bounded handoff required by this mode.")
    print(context)
    prompt = (
        "Read this bounded Continuum handoff before acting. Continue the existing task from its current state. "
        "Use targeted Continuum MCP/context retrieval when more detail is needed; do not restart work or request all history.\n\n"
        + context
    )
    store.event("context_injected", {"agent": args.agent, "mode": args.mode, "summary": f"Injected {estimate_tokens(prompt)} estimated tokens."})
    return (
        run_interactive_agent(args, resumed=True, injected_context=prompt)
        if args.interactive
        else run_agent(args, resumed=True, injected_context=prompt)
    )


def chat(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)
    mode = "compact"
    message_parts = list(args.message)
    if args.mode_or_message:
        if args.mode_or_message in {"compact", "normal", "deep"}:
            mode = args.mode_or_message
        else:
            message_parts.insert(0, args.mode_or_message)
    message = " ".join(message_parts).strip()
    if not message:
        raise SystemExit("Message is empty. Example: `continuum chat claude hi`.")
    context = store.resume_context(mode)
    prompt = (
        "This is a plain Continuum chat question, not a request to continue project work. "
        "Read this bounded Continuum context, then answer the user's message. "
        "For this chat turn, the supplied Continuum context below satisfies the project startup requirement to read `.continuum/current.md` and any included handoff. "
        "Do not refuse by asking to read `.continuum/current.md`, `.continuum/latest_handoff.md`, `GEMINI.md`, `CLAUDE.md`, or `AGENTS.md`; use the supplied context instead. "
        "Do not run shell commands, repo tools, MCP tools, or agent tools unless the user explicitly asks you to inspect or change files. "
        "If the supplied context is not enough, say what context is missing.\n\n"
        "Supplied Continuum context:\n"
        + context
        + "\n\nUser message:\n"
        + message
    )
    print(f"Chat target: {args.agent}")
    print(f"Context mode: {mode}")
    print(f"Estimated prompt: {estimate_tokens(prompt)} tokens")
    store.event(
        "context_injected",
        {"agent": args.agent, "mode": mode, "summary": f"Chat prompt: {message[:120]}"},
    )
    args.agent_args = []
    args.context_limit = None
    args.threshold = None
    return (
        run_interactive_agent(args, resumed=True, injected_context=prompt)
        if args.interactive
        else run_agent(args, resumed=True, injected_context=prompt)
    )


def status(args: argparse.Namespace) -> int:
    store = store_from(args)
    result = project_status(store)
    print(f"Project path: {result['project']}")
    print(f"Network policy: {result['network_policy']}")
    print(f"Daemon state: {result['daemon_state']}")
    print(f"Daemon PID: {result['daemon_pid'] or '-'}")
    print(f"SQLite state: {result['sqlite']}")
    print(f"MCP availability: {result['mcp']}")
    print(f"Configured providers: {', '.join(result['providers']) or 'none'}")
    print(f"Provider health summary: {', '.join(result['provider_health']) or 'none configured'}")
    print(f"Active team: {result['active_team']}")
    print(f"Open tasks: {result['open_tasks']}")
    print(f"Running tasks: {result['running_tasks']}")
    print(f"Claimed files: {result['claimed_files']}")
    print(f"Embedding count: {result['embedding_count']}")
    print(f"Workflow count: {result['workflow_count']}")
    print(f"Message count: {result['message_count']}")
    print(f"Attached external sessions: {result['external_sessions']}")
    print(f"Latest handoff path: {result['handoff_path']}")
    print(f"Latest handoff mirror path: {result['mirror_path']}")
    if args.events:
        print("Recent events:")
        for event in store.recent_events(args.limit):
            detail = event["payload"].get("summary") or event["payload"].get("task") or ""
            print(f"  {event['created_at']} {event['kind']}: {detail}")
    return 0


def doctor(args: argparse.Namespace) -> int:
    checks = run_doctor(store_from(args))
    # Only install/config failures (FAIL) drive a non-zero exit so `continuum doctor`
    # works as a CI install-health gate. Runtime state such as a stopped daemon is
    # reported as INFO and never fails the exit code.
    failed = False
    for item in checks:
        print(f"[{item['status']}] {item['name']}: {item['detail']}")
        if item["fix"]:
            print(f"  Next action: {item['fix']}")
        if item["status"] == "FAIL":
            failed = True
    print("Doctor result: FAIL" if failed else "Doctor result: PASS")
    return 1 if failed else 0


def search(args: argparse.Namespace) -> int:
    store = store_from(args)
    results = store.search(args.query, args.limit)
    for result in results:
        print(f"M{result['id']} {result['created_at']} {result['kind']}: {json.dumps(result['payload'])}")
    if not results:
        print("No matching local memory events.")
    return 0


def print_task(task: dict[str, object]) -> None:
    print(f"{task['task_id']} {task['status']} [{task['mode']}] {task['title']}")
    if task.get("agent"):
        print(f"  agent: {task['agent']}")
    if task.get("branch"):
        print(f"  branch: {task['branch']}")
    if task.get("summary"):
        print(f"  summary: {task['summary']}")
    for lock in task.get("locked_files", []):
        print(f"  lock: {lock['path']} ({lock['agent']})")


def task_create(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)
    print_task(store.create_task(args.title, args.mode))
    return 0


def task_list(args: argparse.Namespace) -> int:
    tasks = store_from(args).list_tasks(args.status, args.limit)
    for task in tasks:
        print_task(task)
    if not tasks:
        print("No tasks found.")
    return 0


def task_show(args: argparse.Namespace) -> int:
    task = store_from(args).get_task(args.task_id)
    if not task:
        raise SystemExit(f"Unknown task: {args.task_id}")
    print_task(task)
    return 0


def task_assign(args: argparse.Namespace) -> int:
    print_task(store_from(args).assign_task(args.task_id, args.agent, args.branch))
    return 0


def task_claim(args: argparse.Namespace) -> int:
    print_task(store_from(args).claim_files(args.task_id, args.agent, args.files, args.expires_at))
    return 0


def task_status(args: argparse.Namespace) -> int:
    print_task(store_from(args).set_task_status(args.task_id, args.status, args.summary))
    return 0


def task_complete(args: argparse.Namespace) -> int:
    print_task(store_from(args).set_task_status(args.task_id, "DONE", args.summary))
    return 0


def claim_list(args: argparse.Namespace) -> int:
    claims = store_from(args).list_claims()
    for claim in claims:
        print(f"{claim['path']}")
        print(f"  task: {claim['task_id']} ({claim['task_status'] or 'unknown'})")
        print(f"  agent: {claim['agent']}")
        print(f"  since: {claim['since']}")
        if claim.get("expires_at"):
            print(f"  expires: {claim['expires_at']}")
    if not claims:
        print("No active file claims.")
    return 0


def claim_release(args: argparse.Namespace) -> int:
    result = store_from(args).release_claim(args.task_id, args.reason, file=args.file, force=args.force)
    if not result["released"]:
        print(f"No matching claims to release for {result['task_id']}.")
        return 0
    print(f"Released {len(result['released'])} claim(s) for {result['task_id']}:")
    for path in result["released"]:
        print(f"  {path}")
    print(f"Reason recorded: {result['reason']}")
    return 0


def claim_recover(args: argparse.Namespace) -> int:
    recovered = store_from(args).recover_stale_claims(args.reason)
    if not recovered:
        print("No orphaned claims found for terminal/failed tasks.")
        return 0
    for item in recovered:
        print(f"{item['task_id']} ({item['task_status']}): released {len(item['released'])} claim(s)")
        for path in item["released"]:
            print(f"  {path}")
    print(f"Reason recorded: {args.reason}")
    return 0


def print_external_session(session: dict[str, object]) -> None:
    print(f"{session['session_id']} {session['status']} {session['agent']} PID={session['pid']}")
    print(f"  cwd: {session.get('cwd') or 'unavailable'}")
    if session.get("context_path"):
        print(f"  context: {session['context_path']}")


def session_detect(args: argparse.Namespace) -> int:
    store = store_from(args)
    sessions = ExternalSessionManager(store).detect(args.all)
    for session in sessions:
        match = "project-match" if session["project_match"] else "other-project"
        print(f"PID={session['pid']} {session['agent']} {match} cwd={session.get('cwd') or 'unavailable'}")
    if not sessions:
        print("No supported live agent sessions detected for this scope.")
        if not args.all:
            print("Next action: run `continuum session detect --all` to inspect agents started from other directories.")
    return 0


def session_attach(args: argparse.Namespace) -> int:
    result = ExternalSessionManager(store_from(args)).attach(args.pid, args.mode, args.allow_other_project)
    session = result["session"]
    packet = result["packet"]
    print_external_session(session)
    print(f"Context packet published: {packet['path']}")
    print(f"Estimated context: {packet['estimated_tokens']} tokens ({packet['mode']})")
    print("Integration mode: cooperative MCP/context packet.")
    print("In the existing agent terminal, enter:")
    print(f'Read "{packet["path"]}" and continue from its Continuum context. Use Continuum MCP for targeted retrieval.')
    print("Continuum cannot retroactively capture or type into a terminal process it did not start.")
    return 0


def session_list(args: argparse.Namespace) -> int:
    sessions = ExternalSessionManager(store_from(args)).refresh()
    for session in sessions:
        print_external_session(session)
    if not sessions:
        print("No external sessions attached.")
    return 0


def session_refresh(args: argparse.Namespace) -> int:
    sessions = ExternalSessionManager(store_from(args)).refresh()
    running = sum(1 for session in sessions if session["status"] == "ATTACHED")
    print(f"Refreshed {len(sessions)} external session(s); {running} attached.")
    return 0


def session_inject(args: argparse.Namespace) -> int:
    store = store_from(args)
    ExternalSessionManager(store).refresh()
    packet = store.publish_external_session_context(args.session_id, args.mode)
    session = packet["session"]
    print(f"Published bounded context for {session['session_id']} ({session['agent']}, PID={session['pid']}).")
    print(f"Context packet: {packet['path']}")
    print(f"Estimated context: {packet['estimated_tokens']} tokens ({packet['mode']})")
    print("Delivery requires the attached session to read the packet directly or through Continuum MCP.")
    print(f'Existing terminal instruction: Read "{packet["path"]}" and continue from this context.')
    return 0


def session_detach(args: argparse.Namespace) -> int:
    session = ExternalSessionManager(store_from(args)).detach(args.session_id)
    print(f"{session['session_id']} DETACHED {session['agent']} PID={session['pid']}")
    print("Continuum will no longer treat this external process as attached.")
    return 0


def providers_list(args: argparse.Namespace) -> int:
    manager = ProviderManager(store_from(args).state_dir)
    config = manager.read()
    for name, provider in config["providers"].items():
        state = "enabled" if provider.get("enabled") else "disabled"
        print(f"{name}: {provider.get('kind')} / {provider.get('type')} / {state}")
    return 0


def providers_add(args: argparse.Namespace) -> int:
    value = ProviderManager(store_from(args).state_dir).add(args.provider)
    print(f"Enabled {args.provider}: {value.get('type')} {value.get('base_url', value.get('command', ''))}")
    return 0


def providers_test(args: argparse.Namespace) -> int:
    store = store_from(args)
    manager = ProviderManager(store.state_dir, store=store)
    names = [args.provider] if args.provider else ["ollama", "openrouter"]
    failed = False
    for name in names:
        try:
            print(f"{name}: {manager.test(name)}")
        except ProviderError as error:
            failed = True
            print(f"{name}: unavailable ({error})")
    return 1 if failed else 0


def model_ask(args: argparse.Namespace) -> int:
    store = store_from(args)
    prompt = " ".join(args.prompt).strip() if isinstance(args.prompt, list) else str(args.prompt)
    if not prompt:
        raise ProviderError("Prompt is empty. Example: `continuum model ask ollama \"summarize this handoff\"`.")
    answer = ProviderManager(store.state_dir, store=store).ask(args.provider, prompt, args.model)
    store.event("model_ask", {"provider": args.provider, "model": args.model, "summary": prompt[:120]})
    print(answer)
    return 0


def memory_embed(args: argparse.Namespace) -> int:
    store = store_from(args)
    source = args.text
    if not source:
        path = store.state_dir / "current.md"
        if not path.exists():
            raise ProviderError("No `current.md` exists. Initialize the project or provide `--text`.")
        source = path.read_text(encoding="utf-8")
    model, vector = ProviderManager(store.state_dir).embed(args.provider, source, args.model)
    key = "text:" + hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
    store.store_embedding(key, args.provider, model, vector, source)
    print(f"Stored {key}: {len(vector)} dimensions via {args.provider}/{model}")
    return 0


def memory_retrieve(args: argparse.Namespace) -> int:
    store = store_from(args)
    if args.semantic:
        try:
            model, vector = ProviderManager(store.state_dir).embed("ollama", args.query, args.model)
            results = store.semantic_search(vector, args.limit, args.query)
            lines = [f"{item.get('memory_id') or item['memory_key']} source={item['source']} score={item['score']}: {item['preview']}" for item in results]
            text = "\n".join(lines)
            budgeted = text[: 2_000 * 4]
            print(f"Semantic retrieval via ollama/{model}: {len(results)} result(s); estimated context <= 2000 tokens")
            print(budgeted)
        except ProviderError as error:
            print(f"Semantic retrieval unavailable ({error}). Falling back to exact local search.")
            results = store.search(args.query, args.limit)
            for item in results:
                print(f"M{item['id']} {item['kind']}: {json.dumps(item['payload'], ensure_ascii=True)}")
    else:
        results = store.search(args.query, args.limit)
        print(f"Exact retrieval: {len(results)} result(s)")
        for item in results:
            print(f"M{item['id']} {item['kind']}: {json.dumps(item['payload'], ensure_ascii=True)}")
    return 0


def memory_refresh(args: argparse.Namespace) -> int:
    store = store_from(args)
    manager = ProviderManager(store.state_dir)
    events = list(reversed(store.recent_events(args.limit)))
    stored = 0
    for event in events:
        payload = json.dumps(event["payload"], ensure_ascii=True)
        source = f"{event['kind']}: {payload}"
        if not source.strip():
            continue
        model, vector = manager.embed(args.provider, source, args.model)
        key = f"M:{event['id']}"
        store.store_embedding(key, args.provider, model, vector, source)
        stored += 1
    print(f"Refreshed {stored} memory embedding(s) via {args.provider}.")
    return 0


def policy_init(args: argparse.Namespace) -> int:
    store = store_from(args)
    store.state_dir.mkdir(parents=True, exist_ok=True)
    path = write_starter_policy(store.state_dir, args.force)
    print(f"Wrote starter policy: {path}")
    print("Edit denied_files / sensitive_globs / allowed_providers as needed, then `continuum policy show`.")
    return 0


def policy_show(args: argparse.Namespace) -> int:
    store = store_from(args)
    policy = store.policy()
    source = policy.source or "built-in defaults (no policy.json present)"
    print(f"Policy source: {source}")
    print(json.dumps(policy.as_dict(), indent=2))
    return 0


def secrets_scan_cmd(args: argparse.Namespace) -> int:
    target = args.path
    if target == "-":
        text = sys.stdin.read()
        label = "<stdin>"
    else:
        path = Path(target)
        if not path.exists():
            print(f"Error: file not found: {target}", file=sys.stderr)
            return 2
        text = path.read_text(encoding="utf-8", errors="replace")
        label = str(path)
    findings = scan_text(text)
    if not findings:
        print(f"{label}: no secrets detected.")
        return 0
    print(f"{label}: {len(findings)} potential secret(s) detected.")
    for finding in findings:
        print(f"  {finding.kind}: {finding.preview}")
    return 1


def context_build(args: argparse.Namespace) -> int:
    store = store_from(args)
    packet = store.context_packet(args.role, args.query, args.mode, args.workflow)
    print(f"Context mode: {packet['mode']}")
    print(f"Estimated context: {packet['estimated_tokens']} tokens")
    print(packet["text"])
    # Optional symbol-aware appendix; default packet behavior is unchanged.
    if getattr(args, "intel", False) or getattr(args, "symbols", False):
        intel = gather_context_intel(store, files=args.files or None)
        print()
        print("## Relevant Symbols / Tests / Recent Changes")
        for path, syms in intel["symbols"].items():
            rendered = ", ".join(f"{s['kind']} {s['name']}" for s in syms)
            print(f"- `{path}`: {rendered}")
        if not intel["symbols"]:
            print("- No symbols extracted.")
        print(f"- Tests: {', '.join(intel['tests']) or 'none'}")
        print(
            "- Recent: "
            + ("; ".join(f"{c['sha']} {c['subject']}" for c in intel["recent_commits"]) or "none")
        )
    return 0


def context_enrich(args: argparse.Namespace) -> int:
    store = store_from(args)
    intel = gather_context_intel(store, args.task_ref, args.files or None)
    if args.json:
        print(json.dumps(intel, indent=2, ensure_ascii=True))
        return 0
    print(render_intel(intel))
    return 0


def context_diff(args: argparse.Namespace) -> int:
    store = store_from(args)
    intel_a = gather_context_intel(store, args.ref_a)
    intel_b = gather_context_intel(store, args.ref_b)
    diff = diff_intel(intel_a, intel_b)
    if args.json:
        print(json.dumps(diff, indent=2, ensure_ascii=True))
        return 0
    print(render_diff(diff))
    return 0


def context_score(args: argparse.Namespace) -> int:
    store = store_from(args)
    score = score_intel(store, args.ref)
    if args.json:
        print(json.dumps(score, indent=2, ensure_ascii=True))
        return 0
    print(render_score(score))
    return 0


def instruct(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)
    result = store.create_delegation(
        args.planner,
        args.executor,
        args.goal,
        mode=args.mode,
        budget=args.budget,
        scope=args.scope,
        review=args.review,
        tests=args.tests,
        handoff=args.handoff,
    )
    print(f"Delegation planned: {result['delegation_id']}")
    print(f"Planner: {result['planner']}")
    print(f"Executor: {result['executor']}")
    print(f"Task: {result['task_id']}")
    print(f"Mode: {result['mode']}")
    print(f"Estimated packet: {result['estimated_tokens']} tokens")
    print(f"Graph: {result['graph_path']}")
    print(f"Packet: {result['packet_path']}")
    print()
    print("Next executor instruction:")
    print(f"Read `{result['packet_path']}` and execute only `{result['task_id']}`.")
    print(f"Use `continuum task claim {result['task_id']} <agent> <files...>` before editing.")
    print("Escalate to the planner if the packet scope or constraints are insufficient.")
    return 0


def message_send(args: argparse.Namespace) -> int:
    message = store_from(args).send_message(
        args.sender, args.recipient, args.body, args.kind, args.workflow, args.task_id
    )
    print(f"{message['message_id']} {message['sender']} -> {message['recipient']}: recorded")
    return 0


def message_inbox(args: argparse.Namespace) -> int:
    messages = store_from(args).messages(args.recipient, args.workflow, args.limit)
    for item in messages:
        print(f"{item['message_id']} {item['sender']} -> {item['recipient']} [{item['kind']}]: {item['body']}")
    if not messages:
        print("No messages.")
    return 0


def team_init(args: argparse.Namespace) -> int:
    path = TeamManager(store_from(args)).init(args.preset)
    print(f"Team preset ready: {path}")
    return 0


def team_list(args: argparse.Namespace) -> int:
    manager = TeamManager(store_from(args))
    for name in manager.list():
        print(name)
    return 0


def team_show(args: argparse.Namespace) -> int:
    config = TeamManager(store_from(args)).load(args.team)
    print(json.dumps(config, indent=2))
    return 0


def show_plan(plan: dict[str, object]) -> None:
    print(f"Team: {plan['team_name']}")
    print(f"Classified route: {plan['task_type']}")
    for step in plan["steps"]:
        permissions = "writer" if step["can_edit_files"] else "read/model"
        print(f"{step['order']}. {step['name']} -> {step['provider']} ({step['role']}, {permissions})")


def team_explain(args: argparse.Namespace) -> int:
    show_plan(TeamManager(store_from(args)).explain(args.team, args.request, args.task_type))
    return 0


def team_run(args: argparse.Namespace) -> int:
    store = store_from(args)
    manager = TeamManager(store)
    plan = manager.explain(args.team, args.request, args.task_type)
    show_plan(plan)
    orchestrator = Orchestrator(store)
    try:
        if args.execute:
            workflow = orchestrator.execute(
                args.team, args.request, args.task_type, args.allow_file, args.context_mode
            )
            print(f"Workflow executed: {workflow['workflow_id']} {workflow['status']}")
            print("Steps exchanged bounded context packets and persisted result messages.")
        else:
            workflow = orchestrator.plan(args.team, args.request, args.task_type)
            print(f"Workflow planned: {workflow['workflow_id']}.")
            print("Tasks created. File claims are ready to be assigned with `continuum task claim`.")
            for step in workflow["steps"]:
                print(f"- {step['task_id']} {step['role']}:{step['provider']}")
            print("Open your selected coding agent and run `continuum resume <agent> compact`.")
            print("Automatic provider launching was not requested. Use `--execute` for sequential opt-in execution.")
    except OrchestrationError as error:
        raise SystemExit(f"Workflow stopped: {error}") from error
    return 0


def print_workflow_summary(workflow: dict[str, object]) -> None:
    print(f"{workflow['workflow_id']} {workflow['status']} team={workflow['team']} step={workflow['current_step']} {workflow['request']}")


def workflow_list(args: argparse.Namespace) -> int:
    workflows = store_from(args).list_workflows(args.limit)
    for workflow in workflows:
        print_workflow_summary(workflow)
    if not workflows:
        print("No workflows found.")
    return 0


def workflow_show(args: argparse.Namespace) -> int:
    workflow = store_from(args).get_workflow(args.workflow_id)
    if not workflow:
        raise SystemExit(f"Unknown workflow: {args.workflow_id}")
    print_workflow_summary(workflow)
    for step in workflow["steps"]:
        print(f"{step['order']}. {step['role']}:{step['provider']} [{step['status']}] {step['task_id'] or '-'}")
        if step.get("output"):
            print(f"   output: {step['output']}")
    return 0


def workflow_retry(args: argparse.Namespace) -> int:
    store = store_from(args)
    orchestrator = Orchestrator(store)
    try:
        workflow = orchestrator.retry(
            args.workflow_id,
            allowed_files=args.allow_file,
            context_mode=args.context_mode,
            execute=args.execute,
        )
    except OrchestrationError as error:
        raise SystemExit(f"Workflow retry stopped: {error}") from error
    if args.execute:
        print(f"Workflow resumed: {workflow['workflow_id']} {workflow['status']}")
        print("Remaining steps re-ran from the first failed/incomplete step; completed earlier tasks were preserved.")
    else:
        print(f"Workflow re-planned from the first failed/incomplete step: {workflow['workflow_id']}.")
        print("Earlier completed steps and their tasks were preserved.")
        for step in workflow["steps"]:
            print(f"- {step['order']}. {step['task_id'] or '-'} {step['role']}:{step['provider']} [{step['status']}]")
        print("Re-run with --execute (providers enabled) to resume sequential execution.")
    return 0


def route_explain(args: argparse.Namespace) -> int:
    return team_explain(args)


def worktree_create(args: argparse.Namespace) -> int:
    record = WorktreeManager(store_from(args)).create(args.task_id)
    print(f"{record['task_id']} worktree: {record['path']}")
    print(f"Branch: {record['branch']}")
    return 0


def worktree_schedule(args: argparse.Namespace) -> int:
    schedule = WorktreeManager(store_from(args)).schedule(
        args.objective,
        args.lane or [],
        args.depends_on or [],
        args.context_mode,
    )
    print(f"Schedule planned: {schedule['schedule_id']}")
    print(f"Objective: {schedule['objective']}")
    print("Parallel worktrees:")
    for lane in schedule["lanes"]:
        print(f"  {lane['task_id']} {lane['role']} -> {lane['agent']}")
        print(f"    worktree: {lane['path']}")
        print(f"    branch: {lane['branch']}")
        print(f"    owns: {', '.join(lane.get('owned_paths', [])) or 'none'}")
        print(f"    context: {lane['context_path']}")
        print(f"    resume: continuum worktree resume {lane['task_id']} {lane['agent']} {schedule['context_mode']}")
    print("Automatic parallel provider launching is not enabled. Start each lane explicitly with its resume command.")
    return 0


def worktree_schedules(args: argparse.Namespace) -> int:
    records = WorktreeManager(store_from(args)).schedules()
    for item in records:
        print(f"{item['schedule_id']} {item['status']} lanes={len(item['lanes'])} {item['objective']}")
    if not records:
        print("No worktree schedules.")
    return 0


def worktree_schedule_status(args: argparse.Namespace) -> int:
    schedule = WorktreeManager(store_from(args)).schedule_status(args.schedule_id)
    print(f"{schedule['schedule_id']} {schedule['status']}: {schedule['objective']}")
    for lane in schedule["lanes"]:
        gates = f"tests={lane.get('test_result') or '-'} review={lane.get('review_status') or '-'}"
        deps = ",".join(lane.get("dependencies", [])) or "none"
        print(
            f"  {lane['task_id']} {lane.get('task_status', '-')} {lane['role']} "
            f"{lane['agent']} deps={deps} {gates} merge_ready={lane.get('merge_ready')}"
        )
        print(f"    {lane['branch']} {lane['path']}")
    return 0


def worktree_list(args: argparse.Namespace) -> int:
    records = WorktreeManager(store_from(args)).list()
    for item in records:
        print(f"{item['task_id']} {item['status']} {item['branch']} {item['path']}")
    if not records:
        print("No worktrees.")
    return 0


def worktree_resume(args: argparse.Namespace) -> int:
    store = store_from(args)
    manager = WorktreeManager(store)
    record = manager.diff(args.task_id)
    context_path = record.get("context_path")
    context_text = Path(context_path).read_text(encoding="utf-8") if context_path and Path(context_path).exists() else ""
    packet = store.context_packet(record.get("role", args.agent), record.get("objective", ""), args.mode)
    prompt = (
        "Read this Continuum parallel worktree packet before acting. "
        "Work only inside the assigned worktree and edit only owned paths.\n\n"
        + (context_text or packet["text"])
    )
    args.cwd = Path(record["path"])
    args.agent_args = []
    args.context_limit = None
    args.threshold = None
    print(f"Task: {record['task_id']} role={record.get('role', '-')} agent={args.agent}")
    print(f"Worktree: {record['path']}")
    print(f"Branch: {record['branch']}")
    print(f"Owned paths: {', '.join(record.get('owned_paths', [])) or 'none'}")
    print(f"Estimated context: {estimate_tokens(prompt)} tokens")
    return (
        run_interactive_agent(args, resumed=True, injected_context=prompt)
        if args.interactive
        else run_agent(args, resumed=True, injected_context=prompt)
    )


def worktree_diff(args: argparse.Namespace) -> int:
    record = WorktreeManager(store_from(args)).diff(args.task_id)
    print(f"{record['task_id']} changed files: {', '.join(record.get('changed_files', [])) or 'none'}")
    print(record.get("diff_summary") or "No diff.")
    return 0


def worktree_test_result(args: argparse.Namespace) -> int:
    record = WorktreeManager(store_from(args)).record_tests(args.task_id, args.passed, args.note)
    print(f"{record['task_id']} tests: {record['test_result']} ({record['completion_note']})")
    return 0


def worktree_review(args: argparse.Namespace) -> int:
    record = WorktreeManager(store_from(args)).record_review(args.task_id, args.approved, args.note)
    print(f"{record['task_id']} review: {record['review_status']} ({record['review_note']})")
    return 0


def worktree_merge(args: argparse.Namespace) -> int:
    record = WorktreeManager(store_from(args)).merge(args.task_id)
    print(f"{record['task_id']} merged from {record['branch']}.")
    return 0


def worktree_discard(args: argparse.Namespace) -> int:
    record = WorktreeManager(store_from(args)).discard(args.task_id, args.force)
    print(f"{record['task_id']} discarded; branch removed.")
    return 0


def evidence(args: argparse.Namespace) -> int:
    record = gather_evidence(store_from(args), args.task_id)
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=True))
        return 0
    print(f"{record['task_id']} {record['status']} [{record['mode']}] {record['title']}")
    print(f"  agent: {record['agent'] or '-'}")
    print(f"  branch: {record['branch'] or '-'}")
    print(f"  worktree: {record['worktree_note']}")
    print(f"  claimed files: {', '.join(record['claimed_files']) or 'none'}")
    print(f"  changed files: {', '.join(record['changed_files']) or 'none'}")
    test_gate = record["test_gate"]
    review_gate = record["review_gate"]
    print(f"  test gate: {test_gate['result'] or 'not recorded'} sha={test_gate['sha'] or '-'} note={test_gate['note'] or '-'}")
    print(f"  review gate: {review_gate['result'] or 'not recorded'} sha={review_gate['sha'] or '-'} note={review_gate['note'] or '-'}")
    print("  activity:")
    for event in record["events"]:
        print(f"    {event['created_at']} {event['kind']}: {event['detail']}")
    if not record["events"]:
        print("    none recorded")
    print("  risks:")
    for risk in record["risks"]:
        print(f"    - {risk}")
    if not record["risks"]:
        print("    none detected")
    print(f"  next action: {record['next_action']}")
    return 0


AGENT_TO_PROVIDER = {"claude": "claude_code", "codex": "codex", "gemini": "gemini_cli"}


def _disabled_objective_agents(store: MemoryStore, agents: set[str]) -> list[str]:
    """Return lane agents whose backing provider is not enabled (for --execute)."""
    config = ProviderManager(store.state_dir).read()["providers"]
    disabled = []
    for agent in sorted(agents):
        provider = AGENT_TO_PROVIDER.get(agent, agent)
        if not config.get(provider, {}).get("enabled"):
            disabled.append(agent)
    return disabled


def objective(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)
    if args.execute:
        # Mirror `team run`'s graceful refusal: never launch providers that the
        # project has not explicitly enabled. No traceback, exit non-zero.
        agents = set(_parse_objective_agents(args.agent)) or set(AGENT_PROVIDERS)
        disabled = _disabled_objective_agents(store, agents)
        if disabled:
            raise SystemExit(
                "Objective stopped: --execute needs enabled agent provider(s) for "
                f"{', '.join(disabled)}. Run `continuum providers add <provider>` "
                "(e.g. claude_code, codex, gemini_cli) or omit --execute to plan only."
            )
    try:
        result = plan_objective(
            store,
            args.goal,
            team=args.team,
            task_type=args.task_type,
            agent_specs=args.agent or [],
            path_specs=args.path or [],
            depends_on=args.depends_on or [],
            tests=args.tests,
            review_policy=args.review_policy,
            mode=args.mode,
            context_mode=args.context_mode,
        )
    except ObjectiveError as error:
        raise SystemExit(f"Objective stopped: {error}") from error
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    render_objective(result)
    if args.execute:
        print()
        print("Providers are enabled, but Continuum does not auto-launch parallel lanes.")
        print("Run the NEXT COMMANDS above to drive each lane with full control.")
    return 0


def _parse_objective_agents(specs: list[str] | None) -> list[str]:
    agents = []
    for raw in specs or []:
        if "=" in raw:
            agents.append(raw.split("=", 1)[1].strip())
        elif ":" in raw:
            agents.append(raw.split(":", 1)[1].strip())
    return agents


def render_objective(result: dict[str, object]) -> None:
    print(f"Objective: {result['goal']}")
    print(f"Task type: {result['task_type']} (team {result['team']})")
    print(f"Mode: {result['mode']}  review-policy: {result['review_policy']}  tests: {result['tests'] or 'not set'}")
    if result["scheduled"]:
        print(f"Schedule: {result['schedule_id']}")
    print("Lanes:")
    for lane in result["lanes"]:
        deps = ", ".join(lane.get("depends_on", [])) or "none"
        owned = ", ".join(lane.get("paths", [])) or "none"
        print(f"  {lane['role']} -> {lane['agent']}  owns: {owned}  depends-on: {deps}")
        if lane.get("task_id"):
            print(f"    task: {lane['task_id']}")
        if lane.get("branch"):
            print(f"    branch: {lane['branch']}")
            print(f"    worktree: {lane['worktree']}")
            print(f"    context: {lane['context_path']}")
    print("Timeline (dependency order):")
    print("  " + " -> ".join(result["timeline"]))
    for note in result.get("notes", []):
        print(f"Note: {note}")
    print("NEXT COMMANDS:")
    for command in result["next_commands"]:
        print(f"  {command}")


def pr_packet(args: argparse.Namespace) -> int:
    record = gather_evidence(store_from(args), args.task_id)
    markdown = render_packet(record)
    if args.output:
        write_text(Path(args.output), markdown if markdown.endswith("\n") else markdown + "\n")
        print(f"Wrote PR packet: {args.output}")
    else:
        print(markdown)
    return 0


def flight_record(args: argparse.Namespace) -> int:
    record = gather_flight_record(store_from(args), args.task_id)
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=True))
        return 0
    print(render_flight_record(record))
    return 0


def roi(args: argparse.Namespace) -> int:
    summary = roi_summary(store_from(args))
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=True))
    else:
        print(render_roi(summary))
    return 0


def benchmark_capture(args: argparse.Namespace) -> int:
    capture = capture_task(store_from(args), args.task_id, args.label)
    if args.output:
        write_capture(Path(args.output), capture)
        print(f"Wrote benchmark capture: {args.output}")
    else:
        print(json.dumps(capture, indent=2, ensure_ascii=True))
    return 0


def benchmark_compare(args: argparse.Namespace) -> int:
    comparison = compare_captures(load_capture(Path(args.without)), load_capture(Path(args.with_continuum)))
    if args.json:
        print(json.dumps(comparison, indent=2, ensure_ascii=True))
    else:
        print(render_comparison(comparison))
    return 0


def service(args: argparse.Namespace) -> int:
    manager = ServiceManager(store_from(args))
    result = getattr(manager, args.action)()
    state = "installed" if result.get("installed", args.action == "install") else "not installed"
    print(f"{result['platform']} service {state}: {result['path']}")
    print(f"Next action: {result['next_action']}")
    return 0


def autostart(args: argparse.Namespace) -> int:
    return service(args)


def interactive_shell(args: argparse.Namespace) -> int:
    from .interactive import InteractiveShell

    def dispatch(argv: list[str]) -> int:
        try:
            return main(argv)
        except SystemExit as error:
            return int(error.code) if isinstance(error.code, int) else 1

    shell = InteractiveShell(
        Path(args.project),
        Path(args.vault) if args.vault else None,
        dispatch=dispatch,
        color=args.color,
        animation=args.animation,
        selected_agent=args.agent,
    )
    return shell.run()


def command_classify(args: argparse.Namespace) -> int:
    """Classify a command's risk. Exits non-zero when level==high so scripts can gate.

    When a project policy is reachable, the command's approval-required status is
    also reported using the policy `approval_required_risk` threshold.
    """
    result = command_risk.classify(args.command)
    approval = None
    try:
        policy = store_from(args).policy()
        approval = requires_approval(args.command, policy)["approval_required"]
    except (PolicyError, OSError):
        approval = None
    if args.json:
        payload = dict(result)
        if approval is not None:
            payload["approval_required"] = approval
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    else:
        print(command_risk.render(result))
        if approval is not None:
            print(f"Approval required: {'yes' if approval else 'no'}")
    return 1 if result["level"] == "high" else 0


def _load_trust(store: MemoryStore) -> dict[str, object]:
    return mcp_trust.load_registry(store.state_dir)


def _save_trust(store: MemoryStore, registry: dict[str, object], action: str, server: str, detail: dict[str, object]) -> None:
    mcp_trust.save_registry(store.state_dir, registry)
    store.event("mcp_trust_changed", {"action": action, "server": server, **detail})


def mcp_trust_list(args: argparse.Namespace) -> int:
    registry = _load_trust(store_from(args))
    servers = registry.get("servers", [])
    if not servers:
        print("No MCP servers registered. Unknown servers are treated as untrusted by default.")
        return 0
    for entry in servers:
        print(f"{entry['server']}: {entry['status']} risk={entry['risk_score']} changed={entry['last_changed']}")
        if entry["tool_allow"]:
            print(f"  allow: {', '.join(entry['tool_allow'])}")
        if entry["tool_deny"]:
            print(f"  deny: {', '.join(entry['tool_deny'])}")
    return 0


def mcp_trust_add(args: argparse.Namespace) -> int:
    store = store_from(args)
    registry = _load_trust(store)
    entry = mcp_trust.add_server(registry, args.server, args.status)
    _save_trust(store, registry, "add", entry["server"], {"status": entry["status"]})
    print(f"Added {entry['server']}: {entry['status']}")
    return 0


def mcp_trust_set(args: argparse.Namespace) -> int:
    store = store_from(args)
    registry = _load_trust(store)
    entry = mcp_trust.set_status(registry, args.server, args.status)
    _save_trust(store, registry, "set_status", entry["server"], {"status": entry["status"]})
    print(f"{entry['server']}: {entry['status']}")
    return 0


def mcp_trust_allow(args: argparse.Namespace) -> int:
    store = store_from(args)
    registry = _load_trust(store)
    entry = mcp_trust.allow_tool(registry, args.server, args.tool)
    _save_trust(store, registry, "allow_tool", entry["server"], {"tool": args.tool})
    print(f"{entry['server']}: allow {args.tool}")
    return 0


def mcp_trust_deny(args: argparse.Namespace) -> int:
    store = store_from(args)
    registry = _load_trust(store)
    entry = mcp_trust.deny_tool(registry, args.server, args.tool)
    _save_trust(store, registry, "deny_tool", entry["server"], {"tool": args.tool})
    print(f"{entry['server']}: deny {args.tool}")
    return 0


def mcp_trust_remove(args: argparse.Namespace) -> int:
    store = store_from(args)
    registry = _load_trust(store)
    removed = mcp_trust.remove_server(registry, args.server)
    if not removed:
        print(f"No registry entry for {args.server}.")
        return 0
    _save_trust(store, registry, "remove", args.server, {})
    print(f"Removed {args.server}.")
    return 0


def audit_export_cmd(args: argparse.Namespace) -> int:
    store = store_from(args)
    text = audit_export.export(store, since=args.since, fmt=args.format)
    if args.output:
        write_text(Path(args.output), text if text.endswith("\n") else text + "\n")
        print(f"Wrote audit export: {args.output}")
    else:
        print(text)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="continuum", description="Local context continuity for AI coding agents.")
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = root.add_subparsers(dest="command", required=False)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", default=".", help="Project working directory (default: current directory).")
    common.add_argument("--vault", help="Obsidian folder used for compact mirrored notes.")

    save_cmd = commands.add_parser(
        "save",
        parents=[common],
        help='Save context in plain words before switching AI: continuum save "did X | next do Y".',
    )
    save_cmd.add_argument("text", nargs="*", help="What you were doing. Use ' | ' to separate the next step.")
    save_cmd.set_defaults(func=quick_save)

    go_cmd = commands.add_parser(
        "go", parents=[common], help="Open an agent with your saved context already injected."
    )
    go_cmd.add_argument("agent", choices=AGENTS)
    go_cmd.add_argument("mode", nargs="?", choices=["compact", "normal", "deep"], default="compact")
    go_cmd.add_argument(
        "--no-interactive", action="store_true", help="Run the agent in print mode instead of a live terminal."
    )
    go_cmd.set_defaults(func=go)

    copy_cmd = commands.add_parser(
        "copy", parents=[common], help="Print saved context and copy it to the clipboard for any AI chat."
    )
    copy_cmd.add_argument("mode", nargs="?", choices=["compact", "normal", "deep"], default="compact")
    copy_cmd.set_defaults(func=copy_context)

    setup_cmd = commands.add_parser(
        "setup", parents=[common], help="One-time setup: initialize memory and connect installed agent CLIs."
    )
    setup_cmd.set_defaults(func=setup)

    handoff_llm_cmd = commands.add_parser(
        "handoff-llm",
        help="Configure a dedicated third LLM that writes handoffs and checkpoint context.",
    )
    handoff_llm_commands = handoff_llm_cmd.add_subparsers(dest="handoff_llm_command", required=True)
    set_handoff_llm = handoff_llm_commands.add_parser(
        "set", parents=[common], help="Select the model provider (and optional model) that writes handoffs."
    )
    set_handoff_llm.add_argument("provider", help="Model provider name, for example ollama or openrouter.")
    set_handoff_llm.add_argument("model", nargs="?", help="Optional model ID; defaults to the provider's chat model.")
    set_handoff_llm.set_defaults(func=handoff_llm_set)
    show_handoff_llm = handoff_llm_commands.add_parser(
        "show", parents=[common], help="Show the configured handoff LLM."
    )
    show_handoff_llm.set_defaults(func=handoff_llm_show)
    off_handoff_llm = handoff_llm_commands.add_parser(
        "off", parents=[common], help="Disable the handoff LLM and return to recorded-state handoffs."
    )
    off_handoff_llm.set_defaults(func=handoff_llm_off)

    init = commands.add_parser("init", parents=[common], help="Initialize memory for a project.")
    init.add_argument("--context-limit", type=int, default=DEFAULT_CONTEXT_LIMIT)
    init.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    init.set_defaults(func=initialize)

    daemon_cmd = commands.add_parser("daemon", parents=[common], help="Watch project changes continuously.")
    daemon_cmd.add_argument("--interval", type=float, default=3.0)
    daemon_cmd.set_defaults(func=daemon)

    start = commands.add_parser("up", parents=[common], help="Start the local memory daemon in the background.")
    start.set_defaults(func=up)

    stop = commands.add_parser("down", parents=[common], help="Stop the project memory daemon.")
    stop.set_defaults(func=down)

    output = commands.add_parser("logs", parents=[common], help="Show daemon output.")
    output.add_argument("--tail", type=int, default=50)
    output.set_defaults(func=logs)

    make_handoff = commands.add_parser("handoff", parents=[common], help="Write a deliberate continuation checkpoint.")
    make_handoff.add_argument("--task", required=True)
    make_handoff.add_argument("--next-step", required=True)
    make_handoff.set_defaults(func=handoff)

    for name, handler in (("run", run), ("resume", resume)):
        command = commands.add_parser(name, parents=[common], help=f"{name.title()} an agent through Continuum.")
        command.add_argument("agent", choices=AGENTS)
        if name == "resume":
            command.add_argument("mode", choices=["compact", "normal", "deep"], nargs="?", default="compact")
        command.add_argument("--context-limit", type=int)
        command.add_argument("--threshold", type=float)
        command.add_argument(
            "--interactive",
            "--pty",
            dest="interactive",
            action="store_true",
            help="Run the agent in a real interactive PTY/ConPTY terminal.",
        )
        command.add_argument("agent_args", nargs=argparse.REMAINDER)
        command.set_defaults(func=handler)

    chat_command = commands.add_parser("chat", parents=[common], help="Send one message to an agent with bounded Continuum context.")
    chat_command.add_argument("agent", choices=AGENTS)
    chat_command.add_argument("mode_or_message", nargs="?")
    chat_command.add_argument("--interactive", "--pty", dest="interactive", action="store_true")
    chat_command.add_argument("message", nargs="*")
    chat_command.set_defaults(func=chat)

    show = commands.add_parser("status", parents=[common], help="Show recent project memory activity.")
    show.add_argument("--limit", type=int, default=8)
    show.add_argument("--events", action="store_true", help="Also print recent recorded events.")
    show.set_defaults(func=status)

    check_install = commands.add_parser("doctor", parents=[common], help="Run deterministic release and integration checks.")
    check_install.set_defaults(func=doctor)

    find = commands.add_parser("search", parents=[common], help="Search local recorded memory events.")
    find.add_argument("query")
    find.add_argument("--limit", type=int, default=10)
    find.set_defaults(func=search)

    sessions = commands.add_parser("session", help="Detect and bridge agent CLIs launched outside Continuum.")
    session_commands = sessions.add_subparsers(dest="session_command", required=True)
    detect_session = session_commands.add_parser("detect", parents=[common], help="Detect supported live external agent CLIs.")
    detect_session.add_argument("--all", action="store_true", help="Include supported agents whose working directory is outside this project.")
    detect_session.set_defaults(func=session_detect)
    attach_session = session_commands.add_parser("attach", parents=[common], help="Register a live external agent with shared memory.")
    attach_session.add_argument("pid", type=int)
    attach_session.add_argument("--mode", choices=["compact", "normal", "deep"], default="compact")
    attach_session.add_argument("--allow-other-project", action="store_true")
    attach_session.set_defaults(func=session_attach)
    list_sessions = session_commands.add_parser("list", parents=[common], help="List attached external sessions and liveness.")
    list_sessions.set_defaults(func=session_list)
    refresh_sessions = session_commands.add_parser("refresh", parents=[common], help="Refresh external session liveness.")
    refresh_sessions.set_defaults(func=session_refresh)
    inject_session = session_commands.add_parser("inject", parents=[common], help="Publish bounded context for an attached external session.")
    inject_session.add_argument("session_id")
    inject_session.add_argument("--mode", choices=["compact", "normal", "deep"], default="compact")
    inject_session.set_defaults(func=session_inject)
    detach_session = session_commands.add_parser("detach", parents=[common], help="Stop tracking one attached external session.")
    detach_session.add_argument("session_id")
    detach_session.set_defaults(func=session_detach)

    adapters = commands.add_parser("adapters", help="Inspect supported interactive terminal adapter behavior.")
    adapter_commands = adapters.add_subparsers(dest="adapter_command", required=True)
    list_adapters = adapter_commands.add_parser("list", parents=[common], help="List interactive agent adapter capabilities.")
    list_adapters.set_defaults(func=adapters_list)

    tasks = commands.add_parser("task", help="Manage routed work and exclusive file claims.")
    task_commands = tasks.add_subparsers(dest="task_command", required=True)
    create = task_commands.add_parser("create", parents=[common], help="Create a controlled agent task.")
    create.add_argument("title")
    create.add_argument("--mode", choices=["sequential", "specialist", "parallel"], default="sequential")
    create.set_defaults(func=task_create)
    listing = task_commands.add_parser("list", parents=[common], help="List tasks.")
    listing.add_argument("--status", choices=sorted(TASK_STATUSES))
    listing.add_argument("--limit", type=int, default=20)
    listing.set_defaults(func=task_list)
    show_task = task_commands.add_parser("show", parents=[common], help="Show one task and its file claims.")
    show_task.add_argument("task_id")
    show_task.set_defaults(func=task_show)
    assign = task_commands.add_parser("assign", parents=[common], help="Assign a worker agent.")
    assign.add_argument("task_id")
    assign.add_argument("agent", choices=AGENTS)
    assign.add_argument("--branch")
    assign.set_defaults(func=task_assign)
    claim = task_commands.add_parser("claim", parents=[common], help="Exclusively claim files for a task.")
    claim.add_argument("task_id")
    claim.add_argument("agent", choices=AGENTS)
    claim.add_argument("files", nargs="+")
    claim.add_argument("--expires-at")
    claim.set_defaults(func=task_claim)
    update = task_commands.add_parser("status", parents=[common], help="Advance or finish a task.")
    update.add_argument("task_id")
    update.add_argument("status", choices=sorted(TASK_STATUSES))
    update.add_argument("--summary")
    update.set_defaults(func=task_status)
    complete = task_commands.add_parser("complete", parents=[common], help="Mark a task done and release its file claims.")
    complete.add_argument("task_id")
    complete.add_argument("--summary", required=True)
    complete.set_defaults(func=task_complete)

    claims = commands.add_parser("claim", help="Inspect and recover exclusive file claims.")
    claim_commands = claims.add_subparsers(dest="claim_command", required=True)
    list_claims = claim_commands.add_parser("list", parents=[common], help="List all active file claims.")
    list_claims.set_defaults(func=claim_list)
    release_claim = claim_commands.add_parser("release", parents=[common], help="Release a task's claims (or one file) with an audit reason.")
    release_claim.add_argument("task_id")
    release_claim.add_argument("--reason", required=True, help="Why the claim is being released (recorded as audit event).")
    release_claim.add_argument("--file", help="Release only this one claimed path instead of all of the task's claims.")
    release_claim.add_argument("--force", action="store_true", help="Release even when the owning task is still RUNNING.")
    release_claim.set_defaults(func=claim_release)
    recover_claims = claim_commands.add_parser("recover", parents=[common], help="Release orphaned claims whose owning task is terminal/failed.")
    recover_claims.add_argument("--stale", action="store_true", help="Target orphaned claims of terminal/failed tasks (default behavior).")
    recover_claims.add_argument("--reason", default="Orphaned claim from a terminal or failed task.", help="Audit reason recorded for the recovery.")
    recover_claims.set_defaults(func=claim_recover)

    providers = commands.add_parser("providers", help="Configure agent and model provider backends.")
    provider_commands = providers.add_subparsers(dest="provider_command", required=True)
    list_providers = provider_commands.add_parser("list", parents=[common], help="List configured providers.")
    list_providers.set_defaults(func=providers_list)
    add_provider = provider_commands.add_parser("add", parents=[common], help="Enable a built-in provider in this project's config.")
    add_provider.add_argument("provider", choices=sorted(DEFAULT_PROVIDERS))
    add_provider.set_defaults(func=providers_add)
    test_provider = provider_commands.add_parser("test", parents=[common], help="Test model provider connectivity.")
    test_provider.add_argument("provider", choices=["ollama", "openrouter"], nargs="?")
    test_provider.set_defaults(func=providers_test)

    model = commands.add_parser("model", help="Call a configured model provider without repo-edit permissions.")
    model_commands = model.add_subparsers(dest="model_command", required=True)
    ask = model_commands.add_parser("ask", parents=[common], help="Ask Ollama or OpenRouter a text-only question.")
    ask.add_argument("provider", choices=["ollama", "openrouter"])
    ask.add_argument("prompt", nargs="+")
    ask.add_argument("--model")
    ask.set_defaults(func=model_ask)

    memory = commands.add_parser("memory", help="Structured memory operations.")
    memory_commands = memory.add_subparsers(dest="memory_command", required=True)
    embed = memory_commands.add_parser("embed", parents=[common], help="Store an Ollama embedding for current context.")
    embed.add_argument("provider", choices=["ollama"])
    embed.add_argument("--text")
    embed.add_argument("--model")
    embed.set_defaults(func=memory_embed)
    retrieve = memory_commands.add_parser("retrieve", parents=[common], help="Retrieve exact or Ollama-ranked bounded memory.")
    retrieve.add_argument("query")
    retrieve.add_argument("--limit", type=int, default=8)
    retrieve.add_argument("--semantic", action="store_true", help="Use stored embeddings ranked against an Ollama query embedding.")
    retrieve.add_argument("--model")
    retrieve.set_defaults(func=memory_retrieve)
    refresh = memory_commands.add_parser("refresh", parents=[common], help="Generate or refresh embeddings for recent event memory.")
    refresh.add_argument("provider", choices=["ollama"], nargs="?", default="ollama")
    refresh.add_argument("--limit", type=int, default=20)
    refresh.add_argument("--model")
    refresh.set_defaults(func=memory_refresh)

    context = commands.add_parser("context", help="Create bounded role-specific context packets.")
    context_commands = context.add_subparsers(dest="context_command", required=True)
    build_context = context_commands.add_parser("build", parents=[common], help="Build a compact context packet for one role.")
    build_context.add_argument("role")
    build_context.add_argument("--query", default="")
    build_context.add_argument("--mode", choices=["compact", "normal", "deep"], default="compact")
    build_context.add_argument("--workflow")
    build_context.add_argument(
        "--intel", "--symbols", dest="intel", action="store_true",
        help="Append a compact Relevant Symbols / Tests / Recent Changes section.",
    )
    build_context.add_argument(
        "--file", dest="files", action="append", default=[],
        help="File to inspect for the --intel appendix; repeat per file.",
    )
    build_context.set_defaults(func=context_build)

    enrich_context = context_commands.add_parser(
        "enrich", parents=[common],
        help="Show symbol-aware context intel for a task: files, symbols, tests, commits, decisions, blockers.",
    )
    enrich_context.add_argument("task_ref", nargs="?", help="Task ref (e.g. T0001); omit to use explicit --file paths.")
    enrich_context.add_argument(
        "--file", dest="files", action="append", default=[],
        help="Explicit file to analyze instead of a task's claims; repeat per file.",
    )
    enrich_context.add_argument("--json", action="store_true", help="Emit the intel record as JSON.")
    enrich_context.set_defaults(func=context_enrich)

    diff_context = context_commands.add_parser(
        "diff", parents=[common],
        help="Compare the context two tasks received: files/symbols/tests/decisions only-in-A vs only-in-B and token delta.",
    )
    diff_context.add_argument("ref_a", help="First task ref (e.g. T0001).")
    diff_context.add_argument("ref_b", help="Second task ref (e.g. T0002).")
    diff_context.add_argument("--json", action="store_true", help="Emit the diff as JSON.")
    diff_context.set_defaults(func=context_diff)

    score_context = context_commands.add_parser(
        "score", parents=[common],
        help="Score a task's context packet: tokens, sources, staleness, risk level and missing-info checklist.",
    )
    score_context.add_argument("ref", help="Task ref to score (e.g. T0001).")
    score_context.add_argument("--json", action="store_true", help="Emit the score as JSON.")
    score_context.set_defaults(func=context_score)

    delegate = commands.add_parser(
        "instruct",
        parents=[common],
        help="Create a graph-backed hierarchical delegation packet from a planner to an executor.",
    )
    delegate.add_argument("--planner", required=True, help="Planning model or role, for example claude-opus-4-1-20250805 or gemini-2.5-pro.")
    delegate.add_argument("--executor", required=True, help="Executor agent or provider, for example codex, claude or gemini.")
    delegate.add_argument("--goal", required=True, help="Objective to convert into a bounded execution packet.")
    delegate.add_argument("--mode", choices=["direct", "checkpoint", "supervisor"], default="direct")
    delegate.add_argument("--budget", choices=["low", "normal", "high"], default="normal")
    delegate.add_argument("--scope", action="append", default=[], help="File path the executor may edit; repeat per file.")
    delegate.add_argument("--review", choices=["on-failure", "each-step", "final"], default="on-failure")
    delegate.add_argument("--tests", choices=["required", "best-effort", "none"], default="required")
    delegate.add_argument("--handoff", choices=["strict", "normal"], default="strict")
    delegate.set_defaults(func=instruct)

    messages = commands.add_parser("message", help="Exchange bounded workflow messages between agent roles.")
    message_commands = messages.add_subparsers(dest="message_command", required=True)
    send_message = message_commands.add_parser("send", parents=[common], help="Send a bounded agent result or instruction.")
    send_message.add_argument("sender")
    send_message.add_argument("recipient")
    send_message.add_argument("body")
    send_message.add_argument("--kind", default="result")
    send_message.add_argument("--workflow")
    send_message.add_argument("--task-id")
    send_message.set_defaults(func=message_send)
    inbox = message_commands.add_parser("inbox", parents=[common], help="Read bounded messages addressed to a role.")
    inbox.add_argument("recipient")
    inbox.add_argument("--workflow")
    inbox.add_argument("--limit", type=int, default=20)
    inbox.set_defaults(func=message_inbox)

    team = commands.add_parser("team", help="Configure and plan custom AI engineering teams.")
    team_commands = team.add_subparsers(dest="team_command", required=True)
    init_team = team_commands.add_parser("init", parents=[common], help="Install a built-in JSON team preset.")
    init_team.add_argument("preset", choices=sorted(PRESETS), nargs="?", default="default_dev_team")
    init_team.set_defaults(func=team_init)
    list_teams = team_commands.add_parser("list", parents=[common], help="List project teams.")
    list_teams.set_defaults(func=team_list)
    show_team = team_commands.add_parser("show", parents=[common], help="Print a team's JSON configuration.")
    show_team.add_argument("team")
    show_team.set_defaults(func=team_show)
    explain_team = team_commands.add_parser("explain", parents=[common], help="Explain a team route without creating work.")
    explain_team.add_argument("team")
    explain_team.add_argument("request")
    explain_team.add_argument("--task-type")
    explain_team.set_defaults(func=team_explain)
    run_team = team_commands.add_parser("run", parents=[common], help="Plan a team route or execute it sequentially with explicit consent.")
    run_team.add_argument("team")
    run_team.add_argument("request")
    run_team.add_argument("--task-type")
    run_team.add_argument("--execute", action="store_true", help="Execute providers sequentially using bounded context packets.")
    run_team.add_argument("--allow-file", action="append", default=[], help="Path a writing role may edit during --execute; repeat per file.")
    run_team.add_argument("--context-mode", choices=["compact", "normal", "deep"], default="compact")
    run_team.set_defaults(func=team_run)

    workflows = commands.add_parser("workflow", help="Inspect and resume persisted sequential workflows.")
    workflow_commands = workflows.add_subparsers(dest="workflow_command", required=True)
    list_workflows = workflow_commands.add_parser("list", parents=[common], help="List workflows and their status.")
    list_workflows.add_argument("--limit", type=int, default=20)
    list_workflows.set_defaults(func=workflow_list)
    show_workflow = workflow_commands.add_parser("show", parents=[common], help="Show a workflow's steps and per-step status.")
    show_workflow.add_argument("workflow_id")
    show_workflow.set_defaults(func=workflow_show)
    retry_workflow = workflow_commands.add_parser("retry", parents=[common], help="Resume a FAILED workflow from the first failed/incomplete step.")
    retry_workflow.add_argument("workflow_id")
    retry_workflow.add_argument("--execute", action="store_true", help="Resume sequential provider execution (requires enabled providers).")
    retry_workflow.add_argument("--allow-file", action="append", default=[], help="Path a writing role may edit during --execute; repeat per file.")
    retry_workflow.add_argument("--context-mode", choices=["compact", "normal", "deep"], default="compact")
    retry_workflow.set_defaults(func=workflow_retry)

    worktrees = commands.add_parser("worktree", help="Manage task-isolated Git worktrees.")
    worktree_commands = worktrees.add_subparsers(dest="worktree_command", required=True)
    schedule_worktrees = worktree_commands.add_parser("schedule", parents=[common], help="Plan parallel task worktrees for one objective.")
    schedule_worktrees.add_argument("objective")
    schedule_worktrees.add_argument(
        "--lane",
        action="append",
        help="Parallel lane as role:agent:path1,path2. Repeat for each lane. If omitted, Continuum infers safe lanes from existing repo folders.",
    )
    schedule_worktrees.add_argument("--depends-on", action="append", default=[], help="Dependency as role:depends_on_role; repeat as needed.")
    schedule_worktrees.add_argument("--context-mode", choices=["compact", "normal", "deep"], default="compact")
    schedule_worktrees.set_defaults(func=worktree_schedule)
    list_schedules = worktree_commands.add_parser("schedules", parents=[common], help="List parallel worktree schedules.")
    list_schedules.set_defaults(func=worktree_schedules)
    status_schedule = worktree_commands.add_parser("schedule-status", parents=[common], help="Show lane, gate and merge readiness for a schedule.")
    status_schedule.add_argument("schedule_id")
    status_schedule.set_defaults(func=worktree_schedule_status)
    create_worktree = worktree_commands.add_parser("create", parents=[common], help="Create a task-bound worktree and branch.")
    create_worktree.add_argument("task_id")
    create_worktree.set_defaults(func=worktree_create)
    list_worktrees = worktree_commands.add_parser("list", parents=[common], help="List task worktrees.")
    list_worktrees.set_defaults(func=worktree_list)
    diff_worktree = worktree_commands.add_parser("diff", parents=[common], help="Show changed paths and diff summary for a worktree.")
    diff_worktree.add_argument("task_id")
    diff_worktree.set_defaults(func=worktree_diff)
    resume_worktree = worktree_commands.add_parser("resume", parents=[common], help="Resume an agent inside a task worktree with shared Continuum context.")
    resume_worktree.add_argument("task_id")
    resume_worktree.add_argument("agent", choices=AGENTS)
    resume_worktree.add_argument("mode", choices=["compact", "normal", "deep"], nargs="?", default="compact")
    resume_worktree.add_argument("--interactive", "--pty", dest="interactive", action="store_true")
    resume_worktree.set_defaults(func=worktree_resume)
    test_worktree = worktree_commands.add_parser("test-result", parents=[common], help="Record the required test gate for merging.")
    test_worktree.add_argument("task_id")
    gate = test_worktree.add_mutually_exclusive_group(required=True)
    gate.add_argument("--pass", dest="passed", action="store_true")
    gate.add_argument("--fail", dest="passed", action="store_false")
    test_worktree.add_argument("--note", required=True)
    test_worktree.set_defaults(func=worktree_test_result)
    review_worktree = worktree_commands.add_parser("review", parents=[common], help="Record the required review gate for merging.")
    review_worktree.add_argument("task_id")
    review_gate = review_worktree.add_mutually_exclusive_group(required=True)
    review_gate.add_argument("--approve", dest="approved", action="store_true")
    review_gate.add_argument("--request-changes", dest="approved", action="store_false")
    review_worktree.add_argument("--note", required=True)
    review_worktree.set_defaults(func=worktree_review)
    merge_worktree = worktree_commands.add_parser("merge", parents=[common], help="Merge a tested task worktree into the current branch.")
    merge_worktree.add_argument("task_id")
    merge_worktree.set_defaults(func=worktree_merge)
    discard_worktree = worktree_commands.add_parser("discard", parents=[common], help="Remove an unneeded task worktree and branch.")
    discard_worktree.add_argument("task_id")
    discard_worktree.add_argument("--force", action="store_true")
    discard_worktree.set_defaults(func=worktree_discard)

    evidence_cmd = commands.add_parser("evidence", parents=[common], help="Aggregate inspectable evidence for a task: claims, changes, gates and risks.")
    evidence_cmd.add_argument("task_id")
    evidence_cmd.add_argument("--json", action="store_true", help="Emit the evidence record as JSON.")
    evidence_cmd.set_defaults(func=evidence)

    packet_cmd = commands.add_parser("pr-packet", parents=[common], help="Generate a reviewer-facing markdown PR packet for a task.")
    packet_cmd.add_argument("task_id")
    packet_cmd.add_argument("--output", help="Write the packet to this file instead of stdout.")
    packet_cmd.set_defaults(func=pr_packet)

    flight_cmd = commands.add_parser("flight-record", parents=[common], help="Show a replayable Agent Flight Recorder record for a task.")
    flight_cmd.add_argument("task_id")
    flight_cmd.add_argument("--json", action="store_true", help="Emit the flight record as JSON.")
    flight_cmd.set_defaults(func=flight_record)

    roi_cmd = commands.add_parser("roi", parents=[common], help="Show cost-aware routing and Agent ROI evidence.")
    roi_cmd.add_argument("--json", action="store_true", help="Emit ROI evidence as JSON.")
    roi_cmd.set_defaults(func=roi)

    benchmark_cmd = commands.add_parser("benchmark", help="Capture and compare with-vs-without Continuum task metrics.")
    benchmark_commands = benchmark_cmd.add_subparsers(dest="benchmark_command", required=True)
    capture_benchmark = benchmark_commands.add_parser("capture", parents=[common], help="Capture benchmark metrics from a Continuum task.")
    capture_benchmark.add_argument("task_id")
    capture_benchmark.add_argument("--label", default="with-continuum")
    capture_benchmark.add_argument("--output", help="Write the capture JSON to this path.")
    capture_benchmark.set_defaults(func=benchmark_capture)
    compare_benchmark = benchmark_commands.add_parser("compare", parents=[common], help="Compare without-Continuum and with-Continuum capture JSON files.")
    compare_benchmark.add_argument("--without", required=True, help="Baseline capture JSON.")
    compare_benchmark.add_argument("--with", dest="with_continuum", required=True, help="Continuum capture JSON.")
    compare_benchmark.add_argument("--json", action="store_true", help="Emit comparison JSON.")
    compare_benchmark.set_defaults(func=benchmark_compare)

    objective_cmd = commands.add_parser(
        "objective",
        parents=[common],
        help="Turn one goal into a coordinated, controlled multi-agent plan.",
    )
    objective_cmd.add_argument("goal", help="The objective to plan, for example \"Add billing\".")
    objective_cmd.add_argument("--team", default="default_dev_team", help="Team whose route classifier infers the plan.")
    objective_cmd.add_argument("--task-type", help="Override the inferred route classification.")
    objective_cmd.add_argument(
        "--agent",
        action="append",
        default=[],
        metavar="role=agent",
        help="Lane spec role=agent (agent in claude, codex, gemini). Repeatable. If omitted, derived from the routed roles.",
    )
    objective_cmd.add_argument(
        "--path",
        action="append",
        default=[],
        metavar="role=glob,glob",
        help="Owned paths for a lane as role=glob,glob. Lanes without paths get tasks but no worktree.",
    )
    objective_cmd.add_argument(
        "--depends-on",
        action="append",
        default=[],
        metavar="role:role",
        help="Lane dependency as role:depends_on_role. Repeatable.",
    )
    objective_cmd.add_argument("--tests", help="Required test command recorded for each lane.")
    objective_cmd.add_argument("--review-policy", choices=["all", "risky", "none"], default="all")
    objective_cmd.add_argument(
        "--mode",
        choices=["plan", "schedule"],
        default="plan",
        help="plan = tasks + packets + next-commands; schedule = also create worktree lanes.",
    )
    objective_cmd.add_argument("--context-mode", choices=["compact", "normal", "deep"], default="compact")
    objective_cmd.add_argument(
        "--execute",
        action="store_true",
        help="Only if providers are enabled; otherwise refuses gracefully. Does not auto-launch lanes.",
    )
    objective_cmd.add_argument("--json", action="store_true", help="Emit the plan as JSON.")
    objective_cmd.set_defaults(func=objective)

    policy_cmd = commands.add_parser("policy", help="Manage the governance policy (.continuum/policy.json).")
    policy_commands = policy_cmd.add_subparsers(dest="policy_command", required=True)
    init_policy = policy_commands.add_parser("init", parents=[common], help="Write a starter policy.json.")
    init_policy.add_argument("--force", action="store_true", help="Overwrite an existing policy.json.")
    init_policy.set_defaults(func=policy_init)
    show_policy = policy_commands.add_parser("show", parents=[common], help="Print the effective policy and its source.")
    show_policy.set_defaults(func=policy_show)

    command_cmd = commands.add_parser("command", help="Classify command risk for governance gating.")
    command_commands = command_cmd.add_subparsers(dest="command_command", required=True)
    classify_cmd = command_commands.add_parser("classify", parents=[common], help="Classify a command's risk; exits non-zero if high.")
    classify_cmd.add_argument("command", help="The command string to classify, quoted.")
    classify_cmd.add_argument("--json", action="store_true", help="Emit the classification as JSON.")
    classify_cmd.set_defaults(func=command_classify)

    audit_cmd = commands.add_parser("audit", help="Inspect and export the local event audit trail.")
    audit_commands = audit_cmd.add_subparsers(dest="audit_command", required=True)
    export_cmd = audit_commands.add_parser("export", parents=[common], help="Export the event log as an auditable trail.")
    export_cmd.add_argument("--since", help="Lower time bound: ISO date/time or relative like '7d'.")
    export_cmd.add_argument("--format", choices=["json", "md"], default="md", help="Output format (default: md).")
    export_cmd.add_argument("--output", help="Write to this file instead of stdout.")
    export_cmd.set_defaults(func=audit_export_cmd)

    secrets_cmd = commands.add_parser("secrets", help="Scan text for secrets before it leaves the machine.")
    secrets_commands = secrets_cmd.add_subparsers(dest="secrets_command", required=True)
    scan_secrets = secrets_commands.add_parser("scan", parents=[common], help="Scan a file (or '-' for stdin) for secrets.")
    scan_secrets.add_argument("path", help="Path to scan, or '-' to read from stdin.")
    scan_secrets.set_defaults(func=secrets_scan_cmd)

    route = commands.add_parser("route", help="Explain provider/team routing.")
    route_commands = route.add_subparsers(dest="route_command", required=True)
    explain_route = route_commands.add_parser("explain", parents=[common], help="Classify and show the selected team route.")
    explain_route.add_argument("request")
    explain_route.add_argument("--team", default="default_dev_team")
    explain_route.add_argument("--task-type")
    explain_route.set_defaults(func=route_explain)

    services = commands.add_parser("service", parents=[common], help="Manage the native login service for this project.")
    services.add_argument("action", choices=["install", "status", "remove"])
    services.set_defaults(func=service)

    startup = commands.add_parser("autostart", parents=[common], help="Compatibility alias for native login service installation.")
    startup.add_argument("action", choices=["install", "status", "remove"])
    startup.set_defaults(func=autostart)

    mcp = commands.add_parser("mcp", help="Expose scoped memory tools over MCP stdio and manage MCP trust.")
    mcp_commands = mcp.add_subparsers(dest="mcp_command", required=True)
    serve_mcp = mcp_commands.add_parser("serve", parents=[common], help="Expose scoped memory tools over MCP stdio.")
    serve_mcp.set_defaults(func=lambda args: serve_stdio(store_from(args)))
    trust_mcp = mcp_commands.add_parser("trust", help="Manage the MCP server/tool trust registry.")
    trust_commands = trust_mcp.add_subparsers(dest="mcp_trust_command", required=True)
    trust_list = trust_commands.add_parser("list", parents=[common], help="List registered MCP servers and trust status.")
    trust_list.set_defaults(func=mcp_trust_list)
    trust_add = trust_commands.add_parser("add", parents=[common], help="Register an MCP server (default untrusted).")
    trust_add.add_argument("server")
    trust_add.add_argument("--status", choices=list(mcp_trust.STATUSES), default=mcp_trust.DEFAULT_STATUS)
    trust_add.set_defaults(func=mcp_trust_add)
    trust_set = trust_commands.add_parser("set", parents=[common], help="Set an MCP server's trust status.")
    trust_set.add_argument("server")
    trust_set.add_argument("--status", choices=list(mcp_trust.STATUSES), required=True)
    trust_set.set_defaults(func=mcp_trust_set)
    trust_allow = trust_commands.add_parser("allow", parents=[common], help="Allow a specific tool on a server.")
    trust_allow.add_argument("server")
    trust_allow.add_argument("tool")
    trust_allow.set_defaults(func=mcp_trust_allow)
    trust_deny = trust_commands.add_parser("deny", parents=[common], help="Deny a specific tool on a server (overrides allow).")
    trust_deny.add_argument("server")
    trust_deny.add_argument("tool")
    trust_deny.set_defaults(func=mcp_trust_deny)
    trust_remove = trust_commands.add_parser("remove", parents=[common], help="Remove a server from the trust registry.")
    trust_remove.add_argument("server")
    trust_remove.set_defaults(func=mcp_trust_remove)

    ui = commands.add_parser("ui", parents=[common], help="Run Continuum Control Center locally.")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=7357)
    ui.add_argument("--open", action="store_true", dest="open_browser")
    ui.set_defaults(
        func=lambda args: serve_control_center(
            Path(args.project),
            Path(args.vault) if args.vault else None,
            args.host,
            args.port,
            args.open_browser,
        )
    )
    shell = commands.add_parser("shell", parents=[common], help="Open the interactive slash-command console.")
    shell.add_argument("--agent", choices=AGENTS, default="codex")
    shell.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    shell.add_argument("--animation", choices=["auto", "on", "off"], default="auto")
    shell.set_defaults(func=interactive_shell)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if getattr(args, "func", None) is None:
        return quick_status(argparse.Namespace(project=".", vault=None))
    if getattr(args, "threshold", None) is not None and not 0 < args.threshold <= 1:
        raise SystemExit("--threshold must be greater than 0 and at most 1")
    if getattr(args, "context_limit", None) is not None and args.context_limit <= 0:
        raise SystemExit("--context-limit must be positive")
    try:
        return int(args.func(args))
    except (EvidenceError, ExternalSessionError, FlightRecordError, ObjectiveError, PolicyError, ProviderError, ServiceError, TeamError, WorktreeError, mcp_trust.TrustError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
