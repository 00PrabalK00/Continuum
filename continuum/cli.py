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
from .providers import DEFAULT_PROVIDERS, ProviderError, ProviderManager
from .teams import PRESETS, TeamError, TeamManager
from .control_center import serve_control_center
from .diagnostics import project_status, run_doctor

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


def daemon(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)
    store.event("daemon_started", {"summary": f"Watching {store.project}"})
    print(f"Continuum watching {store.project}. Press Ctrl+C to stop.")
    try:
        while True:
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


def run_agent(args: argparse.Namespace, resumed: bool = False) -> int:
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(args.context_limit, args.threshold)
    config = store.read_config()
    limit = args.context_limit or int(config.get("context_limit", DEFAULT_CONTEXT_LIMIT))
    threshold = args.threshold if args.threshold is not None else float(
        config.get("checkpoint_threshold", DEFAULT_THRESHOLD)
    )
    agent_args = list(args.agent_args)
    if agent_args and agent_args[0] == "--":
        agent_args.pop(0)
    session_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{args.agent}"
    log_file = store.state_dir / "session_logs" / f"{session_id}.log"
    action = "resume" if resumed else "run"
    store.event("agent_start", {"summary": f"{action} {args.agent}", "session": session_id})
    if resumed:
        print(f"Resume context: {store.state_dir / 'latest_handoff.md'}")
    print(f"Recording output: {log_file}")
    tokens = 0
    triggered = False
    tail: list[str] = []
    try:
        command = agent_command(args.agent, agent_args)
        with log_file.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                command,
                cwd=str(store.project),
                stdin=None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            )
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="")
                output.write(line)
                output.flush()
                tokens += estimate_tokens(line)
                tail.append(line)
                tail = tail[-MAX_SESSION_EXCERPT_LINES:]
                if not triggered and tokens >= int(limit * threshold):
                    triggered = True
                    store.event("context_checkpoint", {"summary": f"Estimated tokens: {tokens}"})
                    store.write_handoff(
                        f"`{args.agent}` reached its estimated context checkpoint.",
                        f"Resume with another agent and inspect `{log_file.name}` if needed.",
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


def run(args: argparse.Namespace) -> int:
    return run_agent(args)


def resume(args: argparse.Namespace) -> int:
    store = store_from(args)
    if not (store.state_dir / "latest_handoff.md").exists():
        raise SystemExit("No handoff exists yet. Run `continuum init` or `continuum handoff` first.")
    context = store.resume_context(args.mode)
    print(f"Resume mode: {args.mode}")
    print(f"Estimated context: {estimate_tokens(context)} tokens")
    print("Included: compact project state and the bounded handoff required by this mode.")
    print(context)
    return run_agent(args, resumed=True)


def status(args: argparse.Namespace) -> int:
    store = store_from(args)
    result = project_status(store)
    print(f"Project path: {result['project']}")
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
    manager = ProviderManager(store_from(args).state_dir)
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
    answer = ProviderManager(store.state_dir).ask(args.provider, args.prompt, args.model)
    store.event("model_ask", {"provider": args.provider, "model": args.model, "summary": args.prompt[:120]})
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
    manager = TeamManager(store_from(args))
    plan = manager.explain(args.team, args.request, args.task_type)
    show_plan(plan)
    tasks = manager.plan_tasks(args.team, args.request, args.task_type)
    print("Workflow planned.")
    print("Tasks created. File claims are ready to be assigned with `continuum task claim`.")
    for task in tasks:
        print(f"- {task['task_id']} {task['agent']}: {task['title']}")
    print("Open your selected coding agent and run `continuum resume <agent> compact`.")
    print("Automatic provider launching is not enabled in this version.")
    return 0


def route_explain(args: argparse.Namespace) -> int:
    return team_explain(args)


def autostart(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise SystemExit("Automatic startup installation is currently supported on Windows only.")
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    launcher = startup / "Continuum Daemon.cmd"
    if args.action == "remove":
        if launcher.exists():
            launcher.unlink()
        print(f"Removed: {launcher}")
        return 0
    store = store_from(args)
    if not store.config_file.exists():
        store.initialize(DEFAULT_CONTEXT_LIMIT, DEFAULT_THRESHOLD)
    module_root = Path(__file__).resolve().parents[1]
    command = [
        "@echo off",
        f'set "PYTHONPATH={module_root};%PYTHONPATH%"',
        f'"{sys.executable}" -m continuum up --project "{store.project}"'
        + (f' --vault "{store.vault_dir}"' if store.vault_dir else ""),
        "",
    ]
    write_text(launcher, "\n".join(command))
    print(f"Installed: {launcher}")
    print("Continuum will start for this project after your next Windows sign-in.")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="continuum", description="Local context continuity for AI coding agents.")
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", default=".", help="Project working directory (default: current directory).")
    common.add_argument("--vault", help="Obsidian folder used for compact mirrored notes.")

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
        command.add_argument("agent_args", nargs=argparse.REMAINDER)
        command.set_defaults(func=handler)

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
    ask.add_argument("prompt")
    ask.add_argument("--model")
    ask.set_defaults(func=model_ask)

    memory = commands.add_parser("memory", help="Structured memory operations.")
    memory_commands = memory.add_subparsers(dest="memory_command", required=True)
    embed = memory_commands.add_parser("embed", parents=[common], help="Store an Ollama embedding for current context.")
    embed.add_argument("provider", choices=["ollama"])
    embed.add_argument("--text")
    embed.add_argument("--model")
    embed.set_defaults(func=memory_embed)

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
    run_team = team_commands.add_parser("run", parents=[common], help="Plan controlled tasks for a team route.")
    run_team.add_argument("team")
    run_team.add_argument("request")
    run_team.add_argument("--task-type")
    run_team.set_defaults(func=team_run)

    route = commands.add_parser("route", help="Explain provider/team routing.")
    route_commands = route.add_subparsers(dest="route_command", required=True)
    explain_route = route_commands.add_parser("explain", parents=[common], help="Classify and show the selected team route.")
    explain_route.add_argument("request")
    explain_route.add_argument("--team", default="default_dev_team")
    explain_route.add_argument("--task-type")
    explain_route.set_defaults(func=route_explain)

    startup = commands.add_parser("autostart", parents=[common], help="Install or remove Windows login startup.")
    startup.add_argument("action", choices=["install", "remove"])
    startup.set_defaults(func=autostart)

    mcp = commands.add_parser("mcp", parents=[common], help="Expose scoped memory tools over MCP stdio.")
    mcp.add_argument("action", choices=["serve"])
    mcp.set_defaults(func=lambda args: serve_stdio(store_from(args)))

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
    return root


def main() -> int:
    args = parser().parse_args()
    if getattr(args, "threshold", None) is not None and not 0 < args.threshold <= 1:
        raise SystemExit("--threshold must be greater than 0 and at most 1")
    if getattr(args, "context_limit", None) is not None and args.context_limit <= 0:
        raise SystemExit("--context-limit must be positive")
    try:
        return int(args.func(args))
    except (ProviderError, TeamError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
