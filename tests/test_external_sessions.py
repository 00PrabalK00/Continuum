import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from continuum.core import MemoryStore
from continuum.external_sessions import ExternalSessionError, ExternalSessionManager, classify_agent


class FakeProcess:
    def __init__(self, pid, name, command, cwd, created=10.5, running=True):
        self.pid = pid
        self._name = name
        self._command = command
        self._cwd = cwd
        self._created = created
        self._running = running

    def oneshot(self):
        return nullcontext()

    def name(self):
        return self._name

    def cmdline(self):
        return self._command

    def cwd(self):
        return str(self._cwd)

    def create_time(self):
        return self._created

    def is_running(self):
        return self._running


class ExternalSessionTest(unittest.TestCase):
    def test_classifier_skips_embedded_codex_server_and_detects_agent_clis(self):
        self.assertEqual(classify_agent("claude.exe", ["claude.exe"]), "claude")
        self.assertEqual(classify_agent("codex.exe", ["codex.exe"]), "codex")
        self.assertEqual(classify_agent("node.exe", ["node", "@google/gemini-cli/bundle/gemini.js"]), "gemini")
        self.assertIsNone(classify_agent("codex.exe", ["codex.exe", "app-server"]))

    def test_detect_filters_other_project_sessions_unless_requested(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "app"
            other = Path(temporary) / "other"
            manager = ExternalSessionManager(MemoryStore(project))
            processes = [
                FakeProcess(10, "claude.exe", ["claude"], project),
                FakeProcess(11, "codex.exe", ["codex"], other),
            ]
            with patch("continuum.external_sessions.psutil.process_iter", return_value=processes):
                self.assertEqual([item["pid"] for item in manager.detect()], [10])
                self.assertEqual([item["pid"] for item in manager.detect(True)], [10, 11])

    def test_attach_publishes_bounded_context_and_refreshes_liveness(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "app"
            store = MemoryStore(project)
            store.initialize(1000, 0.8)
            process = FakeProcess(10, "claude.exe", ["claude"], project)
            manager = ExternalSessionManager(store)
            with patch("continuum.external_sessions.psutil.Process", return_value=process):
                attached = manager.attach(10, "compact")
                sessions = manager.refresh()
            path = Path(attached["packet"]["path"])
            self.assertEqual(attached["session"]["session_id"], "S0001")
            self.assertTrue(path.exists())
            self.assertIn("Bounded Context", path.read_text(encoding="utf-8"))
            self.assertEqual(sessions[0]["status"], "ATTACHED")

            stopped = FakeProcess(10, "claude.exe", ["claude"], project, running=False)
            with patch("continuum.external_sessions.psutil.Process", return_value=stopped):
                self.assertEqual(manager.refresh()[0]["status"], "STOPPED")

    def test_attach_rejects_wrong_project_without_explicit_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = ExternalSessionManager(MemoryStore(root / "app"))
            process = FakeProcess(10, "gemini", ["node", "@google/gemini-cli/bundle/gemini.js"], root / "other")
            with patch("continuum.external_sessions.psutil.Process", return_value=process):
                with self.assertRaisesRegex(ExternalSessionError, "allow-other-project"):
                    manager.attach(10)

    def test_auto_register_only_adds_matching_process_once_and_detach_is_stable(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "app"
            store = MemoryStore(project)
            store.initialize(1000, 0.8)
            manager = ExternalSessionManager(store)
            process = FakeProcess(10, "codex.exe", ["codex"], project)
            with patch("continuum.external_sessions.psutil.process_iter", return_value=[process]), patch(
                "continuum.external_sessions.psutil.Process", return_value=process
            ):
                self.assertEqual(len(manager.auto_register()), 1)
                self.assertEqual(manager.auto_register(), [])
                manager.detach("S0001")
                self.assertEqual(manager.refresh()[0]["status"], "DETACHED")

    def test_missing_psutil_has_specific_install_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = ExternalSessionManager(MemoryStore(Path(temporary) / "app"))
            with patch("continuum.external_sessions.psutil", None):
                with self.assertRaisesRegex(ExternalSessionError, "python -m pip install psutil"):
                    manager.detect()


if __name__ == "__main__":
    unittest.main()
