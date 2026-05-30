"""Deterministic project status and environment checks for Continuum."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from . import __version__
from .core import MemoryStore
from .mcp_server import handle_request
from .providers import ProviderError, ProviderManager
from .services import ServiceError, ServiceManager
from .teams import TeamManager

FINAL_TASK_STATES = {"DONE", "FAILED"}
RUNNING_TASK_STATES = {"RUNNING", "TESTING", "REVIEWING"}


def process_running(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def daemon_state(store: MemoryStore) -> tuple[str, int | None]:
    path = store.state_dir / "daemon.pid"
    if not path.exists():
        return "stopped", None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return "invalid pid file", None
    return ("running" if process_running(pid) else "stale pid"), pid


def _read_provider_config(store: MemoryStore) -> dict[str, Any]:
    path = store.state_dir / "providers.json"
    if not path.exists():
        return {"providers": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"providers": {}}


def provider_summary(store: MemoryStore) -> list[str]:
    providers = _read_provider_config(store).get("providers", {})
    output: list[str] = []
    for name, config in providers.items():
        if not config.get("enabled"):
            output.append(f"{name}=disabled")
        elif config.get("kind") == "agent":
            command = str(config.get("command", name))
            output.append(f"{name}={'available' if shutil.which(command) else 'missing CLI'}")
        elif name == "openrouter":
            variable = str(config.get("api_key_env", "OPENROUTER_API_KEY"))
            output.append(f"{name}={'configured' if os.environ.get(variable) else 'missing key'}")
        elif name == "ollama":
            output.append(f"{name}={'configured' if shutil.which('ollama') else 'missing CLI'}")
        else:
            output.append(f"{name}=configured")
    return output


def project_status(store: MemoryStore) -> dict[str, Any]:
    state, pid = daemon_state(store)
    sqlite_status = "missing"
    tasks: list[dict[str, Any]] = []
    embedding_count = 0
    workflow_count = 0
    message_count = 0
    external_sessions = 0
    if store.db_file.exists():
        try:
            migration = store.connect()
            migration.close()
            connection = sqlite3.connect(f"file:{store.db_file}?mode=ro", uri=True)
            connection.execute("SELECT 1 FROM events LIMIT 1").fetchall()
            embedding_count = int(connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])
            workflow_count = int(connection.execute("SELECT COUNT(*) FROM workflows").fetchone()[0])
            message_count = int(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
            external_sessions = int(
                connection.execute("SELECT COUNT(*) FROM external_sessions WHERE status = 'ATTACHED'").fetchone()[0]
            )
            connection.close()
            sqlite_status = "ready"
            tasks = store.list_tasks(limit=500)
        except sqlite3.Error as error:
            sqlite_status = f"error: {error}"
    open_tasks = [task for task in tasks if task["status"] not in FINAL_TASK_STATES]
    running_tasks = [task for task in tasks if task["status"] in RUNNING_TASK_STATES]
    claimed_files = sum(len(task.get("locked_files", [])) for task in open_tasks)
    teams = TeamManager(store).list()
    handoff = store.state_dir / "latest_handoff.md"
    mirror = store.notes_dir / "Latest Handoff.md" if store.notes_dir else None
    provider_config = _read_provider_config(store).get("providers", {})
    return {
        "project": str(store.project),
        "daemon_state": state,
        "daemon_pid": pid,
        "sqlite": sqlite_status,
        "mcp": "available (stdio: `continuum mcp serve`)",
        "providers": list(provider_config),
        "provider_health": provider_summary(store),
        "active_team": teams[0] if teams else "not selected",
        "open_tasks": len(open_tasks),
        "running_tasks": len(running_tasks),
        "claimed_files": claimed_files,
        "embedding_count": embedding_count,
        "workflow_count": workflow_count,
        "message_count": message_count,
        "external_sessions": external_sessions,
        "handoff_path": str(handoff) if handoff.exists() else "missing",
        "mirror_path": str(mirror) if mirror and mirror.exists() else ("disabled" if not mirror else "missing"),
    }


def check(name: str, status: str, detail: str, fix: str = "") -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def _mcp_config_check(agent: str, candidates: list[Path], project: Path) -> dict[str, str]:
    configured = False
    for path in candidates:
        if path.exists() and "continuum" in path.read_text(encoding="utf-8", errors="replace").lower():
            configured = True
            break
    config_paths = ", ".join(str(path) for path in candidates)
    if configured:
        return check(f"{agent} MCP config", "PASS", "Continuum server reference found.")
    command = f'continuum mcp serve --project "{project}"'
    return check(f"{agent} MCP config", "INFO", f"No Continuum reference found in {config_paths}.", f"Configure an MCP stdio server using: {command}")


def run_doctor(store: MemoryStore, package_root: Path | None = None) -> list[dict[str, str]]:
    root = package_root or Path(__file__).resolve().parents[1]
    checks: list[dict[str, str]] = []
    psutil_available = importlib.util.find_spec("psutil") is not None
    checks.append(
        check(
            "External session detector",
            "PASS" if psutil_available else "FAIL",
            "psutil is installed for cross-platform agent discovery." if psutil_available else "psutil is unavailable.",
            "Run `python -m pip install psutil`, then retry `continuum doctor`." if not psutil_available else "",
        )
    )
    docker_compose = root / "docker-compose.yml"
    if docker_compose.exists():
        docker = shutil.which("docker")
        checks.append(
            check(
                "Optional Docker mode",
                "PASS" if docker else "INFO",
                "docker-compose.yml available; host daemon remains default." if docker else "Compose profile is available; Docker is not installed.",
                "Install Docker only if using `docker compose --profile vector up -d`." if not docker else "",
            )
        )
    node = shutil.which("node")
    entrypoint = root / "bin" / "continuum.js"
    if node and entrypoint.exists():
        completed = subprocess.run([node, str(entrypoint), "--version"], capture_output=True, text=True, check=False)
        passed = completed.returncode == 0
        checks.append(check("Node entrypoint", "PASS" if passed else "FAIL", completed.stdout.strip() or completed.stderr.strip(), "Run `npm link` from the Continuum repository." if not passed else ""))
    else:
        checks.append(check("Node entrypoint", "FAIL", "Node or bin/continuum.js is unavailable.", "Install Node.js 18+ and run `npm link`."))

    module = importlib.util.find_spec("continuum")
    checks.append(check("Python import", "PASS" if module else "FAIL", "continuum import is available." if module else "Python cannot import continuum.", "Run `python -m pip install -e .`." if not module else ""))

    package_file = root / "package.json"
    try:
        package = json.loads(package_file.read_text(encoding="utf-8"))
        valid_metadata = package.get("version") == __version__ and bool(package.get("bin", {}).get("continuum"))
    except (OSError, json.JSONDecodeError):
        valid_metadata = False
    checks.append(check("Package metadata", "PASS" if valid_metadata else "FAIL", f"package.json version matches {__version__}." if valid_metadata else "package.json is missing, invalid, or its CLI/version does not match.", "Restore package.json and run `npm pack --dry-run`." if not valid_metadata else ""))

    if not store.config_file.exists():
        checks.append(check("Project initialization", "FAIL", "No .continuum/config.json found.", f'Run `continuum init --project "{store.project}"`.'))
        return checks
    checks.append(check("Project initialization", "PASS", str(store.config_file)))

    try:
        connection = store.connect()
        connection.execute("CREATE TEMP TABLE continuum_doctor(value TEXT)")
        connection.execute("INSERT INTO continuum_doctor(value) VALUES ('ok')")
        connection.execute("SELECT value FROM continuum_doctor").fetchone()
        connection.close()
        checks.append(check("SQLite read/write", "PASS", str(store.db_file)))
    except sqlite3.Error as error:
        checks.append(check("SQLite read/write", "FAIL", str(error), f'Check write permission for "{store.state_dir}".'))

    try:
        store.state_dir.mkdir(parents=True, exist_ok=True)
        with store.jsonl_file.open("a", encoding="utf-8"):
            pass
        checks.append(check("JSONL log access", "PASS", str(store.jsonl_file)))
    except OSError as error:
        checks.append(check("JSONL log access", "FAIL", str(error), f'Check write permission for "{store.state_dir}".'))

    response = handle_request(store, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    mcp_ok = bool(response and response.get("result", {}).get("serverInfo", {}).get("name") == "continuum")
    checks.append(check("MCP server boot", "PASS" if mcp_ok else "FAIL", "MCP initialize response is valid." if mcp_ok else "MCP initialize failed.", f'Run `continuum mcp serve --project "{store.project}"`.' if not mcp_ok else ""))

    state, pid = daemon_state(store)
    # A stopped daemon is a normal runtime state, not an install/config failure, so it
    # surfaces as INFO and must never drive a non-zero `continuum doctor` exit code.
    checks.append(check("Daemon process", "PASS" if state == "running" else "INFO", f"{state}; PID {pid}" if pid else state, f'Run `continuum up --project "{store.project}"`.' if state != "running" else ""))
    try:
        try:
            service_home = Path.home()
        except RuntimeError:
            service_home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or store.project)
        service = ServiceManager(store, home=service_home).status()
        checks.append(
            check(
                "Native service definition",
                "PASS" if service["installed"] else "INFO",
                str(service["path"]),
                f'Run `continuum service install --project "{store.project}"`.' if not service["installed"] else "",
            )
        )
    except ServiceError as error:
        checks.append(check("Native service definition", "INFO", str(error)))
    if store.notes_dir:
        mirror_ok = store.notes_dir.exists()
        checks.append(check("Obsidian mirror path", "PASS" if mirror_ok else "FAIL", str(store.notes_dir), f'Run `continuum init --project "{store.project}" --vault "{store.vault_dir}"`.' if not mirror_ok else ""))
    else:
        checks.append(check("Obsidian mirror path", "INFO", "Obsidian mirroring is disabled.", f'Enable it with `continuum init --project "{store.project}" --vault "<vault>/Agents"`.' ))

    config = _read_provider_config(store).get("providers", {})
    manager = ProviderManager(store.state_dir)
    openrouter = config.get("openrouter", {})
    if not openrouter.get("enabled"):
        checks.append(check("OpenRouter configuration", "INFO", "OpenRouter is disabled.", "Enable it with `continuum providers add openrouter`."))
    else:
        variable = str(openrouter.get("api_key_env", "OPENROUTER_API_KEY"))
        if not os.environ.get(variable):
            checks.append(check("OpenRouter configuration", "FAIL", f"Missing environment variable: {variable}.", f'Run `$env:{variable}="<key>"; continuum providers test openrouter`.'))
        else:
            checks.append(check("OpenRouter configuration", "PASS", f"{variable} is set."))
            try:
                checks.append(check("OpenRouter connectivity", "PASS", manager.test("openrouter")))
            except ProviderError as error:
                checks.append(check("OpenRouter connectivity", "FAIL", str(error), "Run `continuum providers test openrouter` after checking network access and the API key."))

    ollama = config.get("ollama", {})
    ollama_enabled = bool(ollama.get("enabled"))
    ollama_path = shutil.which("ollama")
    checks.append(check("Ollama installation", "PASS" if ollama_path else ("FAIL" if ollama_enabled else "INFO"), ollama_path or "Ollama executable not found.", "Install Ollama, then run `ollama serve`." if not ollama_path else ""))
    if ollama_enabled:
        try:
            models = manager.models("ollama")
            checks.append(check("Ollama API localhost:11434", "PASS", f"Connected; {len(models)} model(s) visible."))
            wanted = [ollama.get("chat_model"), ollama.get("embedding_model")]
            missing = [model for model in wanted if model and model not in models]
            if missing:
                commands = " && ".join(f"ollama pull {model}" for model in missing)
                checks.append(check("Ollama configured models", "FAIL", f"Missing: {', '.join(missing)}.", f"Run `{commands}`."))
            else:
                checks.append(check("Ollama configured models", "PASS", "Configured chat and embedding models are available."))
        except ProviderError as error:
            checks.append(check("Ollama API localhost:11434", "FAIL", str(error), "Run `ollama serve`, then `continuum providers test ollama`."))
            checks.append(check("Ollama configured models", "FAIL", "Cannot inspect models until the Ollama API is available.", "Run `ollama serve`, then pull the configured models."))
    else:
        checks.append(check("Ollama API localhost:11434", "INFO", "Ollama is disabled.", "Enable it with `continuum providers add ollama`."))

    try:
        home = Path.home()
    except RuntimeError:
        home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or store.project)
    checks.extend(
        [
            _mcp_config_check("Claude", [store.project / ".mcp.json", home / ".claude.json"], store.project),
            _mcp_config_check("Gemini", [store.project / ".gemini" / "settings.json", home / ".gemini" / "settings.json"], store.project),
            _mcp_config_check("Codex", [store.project / ".codex" / "config.toml", home / ".codex" / "config.toml"], store.project),
        ]
    )
    return checks
