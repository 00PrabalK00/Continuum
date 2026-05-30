import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from continuum.core import MemoryStore
from continuum.services import ServiceManager


class ServiceManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.home = Path(self.tmp.name) / "home"
        self.store = MemoryStore(self.project)
        self.store.initialize(1000, 0.8)

    def _manager(self, system: str) -> ServiceManager:
        return ServiceManager(self.store, system=system, home=self.home)

    def test_linux_install_writes_valid_systemd_unit(self):
        manager = self._manager("Linux")
        result = manager.install()

        path = Path(result["path"])
        self.assertTrue(path.exists())
        self.assertIn("systemctl --user", result["next_action"])

        lines = path.read_text(encoding="utf-8").splitlines()
        sections = [line.strip() for line in lines if line.strip().startswith("[")]
        self.assertEqual(sections, ["[Unit]", "[Service]", "[Install]"])

        directives = {
            line.split("=", 1)[0]: line.split("=", 1)[1]
            for line in lines
            if "=" in line and not line.startswith("#")
        }
        self.assertEqual(directives["Description"], "Continuum local memory daemon")
        self.assertEqual(directives["Restart"], "on-failure")
        self.assertEqual(directives["WantedBy"], "default.target")
        self.assertIn("continuum", directives["ExecStart"])
        self.assertIn("daemon", directives["ExecStart"])

    def test_linux_status_and_remove_lifecycle(self):
        manager = self._manager("Linux")
        self.assertFalse(manager.status()["installed"])

        manager.install()
        status = manager.status()
        self.assertTrue(status["installed"])
        self.assertIn("Service file present", status["next_action"])

        removed = manager.remove()
        self.assertFalse(manager.status()["installed"])
        self.assertIn("disable --now", removed["next_action"])

    def test_macos_install_writes_valid_plist(self):
        manager = self._manager("Darwin")
        result = manager.install()

        path = Path(result["path"])
        self.assertTrue(path.exists())
        self.assertIn("launchctl bootstrap", result["next_action"])

        payload = plistlib.loads(path.read_bytes())
        self.assertEqual(payload["Label"], f"dev.continuum.{self.store.project_id}")
        self.assertTrue(payload["RunAtLoad"])
        self.assertIsInstance(payload["ProgramArguments"], list)
        self.assertIn("daemon", payload["ProgramArguments"])
        self.assertIn("daemon_logs", payload["StandardOutPath"])
        self.assertIn("daemon_logs", payload["StandardErrorPath"])

        logs_dir = self.store.state_dir / "daemon_logs"
        self.assertTrue(logs_dir.exists())

    def test_macos_remove_returns_bootout_instruction(self):
        manager = self._manager("Darwin")
        manager.install()
        result = manager.remove()
        self.assertIn("launchctl bootout", result["next_action"])

    def test_windows_install_writes_startup_cmd(self):
        manager = self._manager("Windows")
        with patch.dict("os.environ", {"APPDATA": self.tmp.name}):
            result = manager.install()

        path = Path(result["path"])
        self.assertIn(f"Continuum Daemon {self.store.project_id}.cmd", result["path"])
        self.assertTrue(path.exists())

        content = path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("@echo off"))
        self.assertIn("continuum", content)
        self.assertIn("PYTHONPATH", content)

    def test_windows_startup_filename_is_project_scoped(self):
        other_project = Path(self.tmp.name) / "other_project"
        other_store = MemoryStore(other_project)
        other_store.initialize(1000, 0.8)

        path_a = self._manager("Windows").location()
        path_b = ServiceManager(other_store, system="Windows", home=self.home).location()

        self.assertIn(self.store.project_id, path_a.name)
        self.assertIn(other_store.project_id, path_b.name)
        self.assertNotEqual(path_a, path_b)

    def test_vault_dir_added_to_daemon_args(self):
        vault = Path(self.tmp.name) / "vault"
        vault.mkdir()
        self.store.vault_dir = vault

        for system in ("Linux", "Darwin"):
            manager = self._manager(system)
            result = manager.install()
            path = Path(result["path"])
            content = path.read_text(encoding="utf-8") if path.suffix == ".service" else path.read_bytes().decode("utf-8", errors="replace")
            self.assertIn("--vault", content)
            self.assertIn(str(vault), content)

    def test_events_recorded_on_install_and_remove(self):
        manager = self._manager("Linux")
        manager.install()
        events = self.store.recent_events(10)
        kinds = [e["kind"] for e in events]
        self.assertIn("service_installed", kinds)

        manager.remove()
        events = self.store.recent_events(10)
        kinds = [e["kind"] for e in events]
        self.assertIn("service_removed", kinds)


if __name__ == "__main__":
    unittest.main()
