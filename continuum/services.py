"""Native login service files for the host-local Continuum daemon."""

from __future__ import annotations

import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from .core import MemoryStore, write_text


class ServiceError(RuntimeError):
    pass


class ServiceManager:
    def __init__(self, store: MemoryStore, system: str | None = None, home: Path | None = None) -> None:
        self.store = store
        self.system = system or platform.system()
        self.home = home or Path.home()

    def location(self) -> Path:
        if self.system == "Windows":
            appdata = Path(os.environ.get("APPDATA", str(self.home / "AppData/Roaming")))
            return appdata / "Microsoft/Windows/Start Menu/Programs/Startup" / f"Continuum Daemon {self.store.project_id}.cmd"
        if self.system == "Darwin":
            return self.home / "Library/LaunchAgents" / f"dev.continuum.{self.store.project_id}.plist"
        if self.system == "Linux":
            return self.home / ".config/systemd/user" / f"continuum-{self.store.project_id}.service"
        raise ServiceError(f"Unsupported service platform: {self.system}")

    def install(self) -> dict[str, Any]:
        path = self.location()
        path.parent.mkdir(parents=True, exist_ok=True)
        daemon_args = [sys.executable, "-m", "continuum", "daemon", "--project", str(self.store.project)]
        if self.store.vault_dir:
            daemon_args.extend(["--vault", str(self.store.vault_dir)])
        if self.system == "Windows":
            module_root = Path(__file__).resolve().parents[1]
            args = [sys.executable, "-m", "continuum", "up", "--project", str(self.store.project)]
            if self.store.vault_dir:
                args.extend(["--vault", str(self.store.vault_dir)])
            write_text(path, "\n".join(["@echo off", f'set "PYTHONPATH={module_root};%PYTHONPATH%"', subprocess.list2cmdline(args), ""]))
            action = "The startup script will run after the next Windows sign-in."
        elif self.system == "Darwin":
            (self.store.state_dir / "daemon_logs").mkdir(parents=True, exist_ok=True)
            payload = {
                "Label": f"dev.continuum.{self.store.project_id}",
                "ProgramArguments": daemon_args,
                "RunAtLoad": True,
                "StandardOutPath": str(self.store.state_dir / "daemon_logs" / "launchd.stdout.log"),
                "StandardErrorPath": str(self.store.state_dir / "daemon_logs" / "launchd.stderr.log"),
            }
            path.write_bytes(plistlib.dumps(payload))
            action = f"Run `launchctl bootstrap gui/$(id -u) \"{path}\"`."
        else:
            content = "\n".join([
                "[Unit]", "Description=Continuum local memory daemon", "",
                "[Service]", f"ExecStart={subprocess.list2cmdline(daemon_args)}", "Restart=on-failure", "",
                "[Install]", "WantedBy=default.target", "",
            ])
            write_text(path, content)
            action = f"Run `systemctl --user daemon-reload && systemctl --user enable --now {path.name}`."
        self.store.event("service_installed", {"platform": self.system, "path": str(path)})
        return {"platform": self.system, "path": str(path), "next_action": action}

    def status(self) -> dict[str, Any]:
        path = self.location()
        return {
            "platform": self.system,
            "path": str(path),
            "installed": path.exists(),
            "next_action": "Service file present." if path.exists() else "Run `continuum service install`.",
        }

    def remove(self) -> dict[str, Any]:
        path = self.location()
        if path.exists():
            path.unlink()
        if self.system == "Darwin":
            action = f"Run `launchctl bootout gui/$(id -u) \"{path}\"` if the service is currently loaded."
        elif self.system == "Linux":
            action = f"Run `systemctl --user disable --now {path.name}; systemctl --user daemon-reload` if it is active."
        else:
            action = "Startup script removed."
        self.store.event("service_removed", {"platform": self.system, "path": str(path)})
        return {"platform": self.system, "path": str(path), "next_action": action}
