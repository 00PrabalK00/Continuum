import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from continuum.core import MemoryStore
from continuum.services import ServiceManager


class ServiceManagerTest(unittest.TestCase):
    def test_linux_install_status_and_remove_write_systemd_user_unit(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            manager = ServiceManager(store, system="Linux", home=Path(temporary) / "home")

            installed = manager.install()
            status = manager.status()
            unit = Path(installed["path"]).read_text(encoding="utf-8")
            removed = manager.remove()

            self.assertTrue(Path(installed["path"]).exists() is False)
            self.assertTrue(status["installed"])
            self.assertIn("systemctl --user", installed["next_action"])
            self.assertIn("disable --now", removed["next_action"])
            self.assertIn(" daemon ", unit)

    def test_macos_install_writes_launch_agent_plist(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            manager = ServiceManager(store, system="Darwin", home=Path(temporary) / "home")

            result = manager.install()

            content = Path(result["path"]).read_bytes()
            self.assertIn(b"ProgramArguments", content)
            self.assertIn(b"daemon", content)
            self.assertIn("launchctl bootstrap", result["next_action"])

    def test_windows_install_uses_startup_launcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            with patch.dict("os.environ", {"APPDATA": temporary}):
                result = ServiceManager(store, system="Windows", home=Path(temporary)).install()

            self.assertIn("Continuum Daemon.cmd", result["path"])
            self.assertIn("continuum", Path(result["path"]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
