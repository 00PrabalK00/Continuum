"""Read-only local HTTP control center for Continuum project state."""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .core import MemoryStore, compact_text, write_text
from .orchestration import OrchestrationError, Orchestrator
from .providers import ProviderError, ProviderManager
from .teams import TeamError, TeamManager

UI_DIR = Path(__file__).resolve().parent / "ui"


def read_optional(path: Path, limit: int = 8_000) -> str:
    if not path.exists():
        return ""
    return compact_text(path.read_text(encoding="utf-8", errors="replace"), limit)


class ControlCenter:
    """Expose real local Continuum objects and explicit command-parity actions."""

    def __init__(self, project: Path, vault: Path | None = None) -> None:
        self.store = MemoryStore(project, vault)

    def daemon_info(self) -> dict[str, Any]:
        pid_file = self.store.state_dir / "daemon.pid"
        pid = int(pid_file.read_text(encoding="utf-8").strip()) if pid_file.exists() else None
        running = False
        if pid:
            if os.name == "nt":
                import ctypes

                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    running = True
            else:
                try:
                    os.kill(pid, 0)
                    running = True
                except OSError:
                    pass
        return {"running": running, "pid": pid}

    def project_info(self) -> dict[str, Any]:
        config = self.store.read_config()
        branch = None
        if (self.store.project / ".git").exists():
            completed = subprocess.run(
                ["git", "-C", str(self.store.project), "branch", "--show-current"],
                capture_output=True,
                text=True,
                check=False,
            )
            branch = completed.stdout.strip() or None
        return {
            "name": self.store.project.name,
            "path": str(self.store.project),
            "branch": branch,
            "memory_path": str(self.store.state_dir),
            "obsidian_path": str(self.store.notes_dir) if self.store.notes_dir else None,
            "context_limit": config.get("context_limit"),
            "threshold": config.get("checkpoint_threshold"),
        }

    def providers(self) -> list[dict[str, Any]]:
        path = self.store.state_dir / "providers.json"
        if not path.exists():
            return []
        configured = json.loads(path.read_text(encoding="utf-8")).get("providers", {})
        values: list[dict[str, Any]] = []
        for name, provider in configured.items():
            values.append(
                {
                    "name": name,
                    "kind": provider.get("kind"),
                    "type": provider.get("type"),
                    "enabled": provider.get("enabled", False),
                    "model": provider.get("chat_model") or provider.get("default_model") or "default",
                    "base_url": provider.get("base_url"),
                    "command": provider.get("command"),
                    "use_for": provider.get("use_for", []),
                }
            )
        return values

    def teams(self) -> list[dict[str, Any]]:
        manager = TeamManager(self.store)
        result = []
        for name in manager.list():
            team = manager.load(name)
            result.append(
                {
                    "name": name,
                    "mode": team.get("mode"),
                    "agents": team.get("agents", {}),
                    "routing": team.get("routing", {}),
                    "safety": team.get("safety", {}),
                }
            )
        return result

    def memory(self, query: str = "") -> dict[str, Any]:
        results = (self.store.search(query, 20) if query else self.store.recent_events(18)) if self.store.db_file.exists() else []
        return {
            "current": read_optional(self.store.state_dir / "current.md"),
            "handoff": read_optional(self.store.state_dir / "latest_handoff.md"),
            "decisions": read_optional(self.store.project / "DECISIONS.md"),
            "implemented": read_optional(self.store.project / "IMPLEMENTED.md"),
            "open_tasks": read_optional(self.store.project / "TASKS.md"),
            "results": results,
        }

    def tasks(self) -> list[dict[str, Any]]:
        return self.store.list_tasks(limit=40) if self.store.db_file.exists() else []

    def events(self) -> list[dict[str, Any]]:
        return list(reversed(self.store.recent_events(35))) if self.store.db_file.exists() else []

    def overview(self) -> dict[str, Any]:
        teams = self.teams()
        tasks = self.tasks()
        events = self.events()
        return {
            "daemon": self.daemon_info(),
            "project": self.project_info(),
            "current_team": teams[0]["name"] if teams else None,
            "latest_handoff": read_optional(self.store.state_dir / "latest_handoff.md", 1_500),
            "providers": self.providers(),
            "tasks": tasks[:6],
            "events": events[:8],
        }

    def create_team(self, preset: str) -> dict[str, Any]:
        path = TeamManager(self.store).init(preset)
        self.store.event("control_center_action", {"summary": f"Created team preset {preset}."})
        return {"team": preset, "path": str(path)}

    def save_team(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        manager = TeamManager(self.store)
        manager.validate(config)
        manager.directory.mkdir(parents=True, exist_ok=True)
        path = manager.directory / f"{name}.json"
        write_text(path, json.dumps(config, indent=2) + "\n")
        self.store.event("control_center_action", {"summary": f"Saved team {name}."})
        return {"team": name, "path": str(path)}

    def test_provider(self, name: str) -> dict[str, str]:
        result = ProviderManager(self.store.state_dir).test(name)
        self.store.event("control_center_action", {"summary": f"Tested provider {name}: {result}"})
        return {"provider": name, "result": result}

    def plan_workflow(self, team: str, request: str, execute: bool, allow_files: list[str]) -> dict[str, Any]:
        runner = Orchestrator(self.store)
        result = runner.execute(team, request, allowed_files=allow_files) if execute else runner.plan(team, request)
        self.store.event("control_center_action", {"summary": f"{'Executed' if execute else 'Planned'} workflow {result['workflow_id']}."})
        return result

    def resume_context(self, role: str, mode: str) -> dict[str, Any]:
        return self.store.context_packet(role, mode=mode)


class ControlCenterHandler(BaseHTTPRequestHandler):
    server: "ControlCenterServer"

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api_get(parsed.path, parse_qs(parsed.query))
            return
        relative = "index.html" if parsed.path in {"/", ""} else parsed.path.lstrip("/")
        path = (UI_DIR / relative).resolve()
        if UI_DIR not in path.parents and path != UI_DIR:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not path.exists() or not path.is_file():
            path = UI_DIR / "index.html"
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        app = self.server.app
        handlers = {
            "/api/overview": app.overview,
            "/api/project": app.project_info,
            "/api/providers": app.providers,
            "/api/teams": app.teams,
            "/api/tasks": app.tasks,
            "/api/events": app.events,
        }
        try:
            if path == "/api/memory":
                self.send_json(app.memory(query.get("q", [""])[0]))
            elif path in handlers:
                self.send_json(handlers[path]())
            else:
                self.send_json({"error": "Unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except (ProviderError, TeamError, ValueError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if parsed.path == "/api/teams/create":
                self.send_json(self.server.app.create_team(str(payload.get("preset", "default_dev_team"))))
            elif parsed.path == "/api/teams/save":
                self.send_json(self.server.app.save_team(str(payload.get("name", "")), dict(payload.get("config", {}))))
            elif parsed.path == "/api/providers/test":
                self.send_json(self.server.app.test_provider(str(payload.get("provider", ""))))
            elif parsed.path == "/api/workflows/run":
                self.send_json(
                    self.server.app.plan_workflow(
                        str(payload.get("team", "")), str(payload.get("request", "")),
                        bool(payload.get("execute", False)), [str(item) for item in payload.get("allow_files", [])],
                    )
                )
            elif parsed.path == "/api/resume-context":
                self.send_json(self.server.app.resume_context(str(payload.get("role", "coder")), str(payload.get("mode", "compact"))))
            else:
                self.send_json({"error": "Unknown action endpoint."}, HTTPStatus.NOT_FOUND)
        except (json.JSONDecodeError, OrchestrationError, ProviderError, TeamError, ValueError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)


class ControlCenterServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], app: ControlCenter) -> None:
        super().__init__(address, ControlCenterHandler)
        self.app = app


def serve_control_center(
    project: Path,
    vault: Path | None,
    host: str = "127.0.0.1",
    port: int = 7357,
    open_browser: bool = False,
) -> int:
    app = ControlCenter(project, vault)
    server = ControlCenterServer((host, port), app)
    url = f"http://{host}:{server.server_port}"
    print(f"Continuum Control Center: {url}")
    print(f"Project: {app.store.project}")
    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
