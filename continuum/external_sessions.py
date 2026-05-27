"""Discovery and safe bridging for agent CLIs launched outside Continuum."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # npm launcher users may not have installed Python dependencies yet.
    psutil = None  # type: ignore[assignment]

from .core import MemoryStore


class ExternalSessionError(ValueError):
    """Raised when an external process cannot be safely registered."""


def require_process_support() -> None:
    if psutil is None:
        raise ExternalSessionError(
            "External session detection requires psutil. "
            "Run `python -m pip install psutil`, then retry `continuum session detect`."
        )


def classify_agent(name: str, command_line: list[str]) -> str | None:
    text = " ".join(command_line).lower()
    executable = name.lower()
    if "app-server" in text:
        return None
    if executable.startswith("claude") or "claude.exe" in text:
        return "claude"
    if executable.startswith("codex") or "codex.exe" in text:
        return "codex"
    if "gemini-cli" in text or "bundle/gemini.js" in text.replace("\\", "/"):
        return "gemini"
    return None


def _candidate(process: psutil.Process) -> dict[str, Any] | None:
    require_process_support()
    try:
        with process.oneshot():
            name = process.name()
            command = process.cmdline()
            agent = classify_agent(name, command)
            if not agent:
                return None
            try:
                cwd = process.cwd()
            except (psutil.AccessDenied, psutil.Error):
                cwd = None
            return {
                "pid": process.pid,
                "process_created_at": process.create_time(),
                "agent": agent,
                "cwd": cwd,
                "command_line": " ".join(command),
            }
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def process_matches_project(candidate: dict[str, Any], project: Path) -> bool:
    cwd = candidate.get("cwd")
    if not cwd:
        return False
    try:
        current = Path(str(cwd)).resolve()
    except OSError:
        return False
    return current == project.resolve() or project.resolve() in current.parents


class ExternalSessionManager:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def detect(self, include_other_projects: bool = False) -> list[dict[str, Any]]:
        require_process_support()
        candidates = []
        for process in psutil.process_iter():
            item = _candidate(process)
            if not item:
                continue
            item["project_match"] = process_matches_project(item, self.store.project)
            if include_other_projects or item["project_match"]:
                candidates.append(item)
        return sorted(candidates, key=lambda item: (item["agent"], item["pid"]))

    def attach(self, pid: int, mode: str = "compact", allow_other_project: bool = False) -> dict[str, Any]:
        require_process_support()
        try:
            candidate = _candidate(psutil.Process(pid))
        except psutil.NoSuchProcess as error:
            raise ExternalSessionError(f"No live process exists with PID {pid}.") from error
        if not candidate:
            raise ExternalSessionError(
                f"PID {pid} is not a supported live Claude Code, Codex or Gemini CLI process."
            )
        if not process_matches_project(candidate, self.store.project) and not allow_other_project:
            raise ExternalSessionError(
                f"PID {pid} is not running inside `{self.store.project}`. "
                "Retry with `--allow-other-project` only if this session is intentionally working on this project."
            )
        if not self.store.config_file.exists():
            self.store.initialize(100_000, 0.80)
        session = self.store.register_external_session(
            candidate["pid"],
            candidate["process_created_at"],
            candidate["agent"],
            candidate.get("cwd"),
            candidate["command_line"],
        )
        packet = self.store.publish_external_session_context(session["session_id"], mode)
        return {"session": session, "packet": packet, "project_match": process_matches_project(candidate, self.store.project)}

    def auto_register(self) -> list[dict[str, Any]]:
        attached = []
        for candidate in self.detect():
            existing = self.store.find_external_session(candidate["pid"], candidate["process_created_at"])
            if existing:
                continue
            session = self.store.register_external_session(
                candidate["pid"],
                candidate["process_created_at"],
                candidate["agent"],
                candidate.get("cwd"),
                candidate["command_line"],
            )
            self.store.publish_external_session_context(session["session_id"], "compact")
            attached.append(session)
        self.refresh()
        return attached

    def detach(self, session_ref: str) -> dict[str, Any]:
        session = self.store.get_external_session(session_ref)
        if not session:
            raise ExternalSessionError(f"Unknown external session: {session_ref}")
        detached = self.store.update_external_session_status(session_ref, "DETACHED")
        self.store.event("external_session_detached", {"session_id": session_ref.upper(), "pid": session["pid"]})
        return detached

    def refresh(self) -> list[dict[str, Any]]:
        require_process_support()
        refreshed = []
        for session in self.store.list_external_sessions():
            if session["status"] == "DETACHED":
                refreshed.append(session)
                continue
            alive = False
            try:
                process = psutil.Process(int(session["pid"]))
                alive = process.is_running() and abs(process.create_time() - float(session["process_created_at"])) < 0.001
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                alive = False
            status = "ATTACHED" if alive else "STOPPED"
            if session["status"] != status:
                session = self.store.update_external_session_status(session["session_id"], status)
                self.store.event("external_session_status", {"session_id": session["session_id"], "status": status})
            refreshed.append(session)
        return refreshed
