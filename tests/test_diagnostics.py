import tempfile
import unittest
import shutil
from pathlib import Path
from unittest.mock import patch

from continuum.core import MemoryStore
from continuum.diagnostics import project_status, run_doctor
from continuum.providers import ProviderError, ProviderManager


class DiagnosticsTest(unittest.TestCase):
    def test_status_counts_claims_and_embeddings(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            task = store.create_task("Patch auth")
            store.claim_files(task["task_id"], "codex", ["auth.py"])
            store.store_embedding("M1", "ollama", "embed", [0.1], "memory")
            store.register_external_session(22, 1.0, "codex", str(store.project), "codex")

            result = project_status(store)

            self.assertEqual(result["open_tasks"], 1)
            self.assertEqual(result["running_tasks"], 1)
            self.assertEqual(result["claimed_files"], 1)
            self.assertEqual(result["embedding_count"], 1)
            self.assertEqual(result["external_sessions"], 1)

    def test_doctor_reports_missing_openrouter_key_with_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            ProviderManager(store.state_dir).add("openrouter")

            with patch.dict("os.environ", {}, clear=True):
                results = run_doctor(store)

            check = next(item for item in results if item["name"] == "OpenRouter configuration")
            self.assertEqual(check["status"], "FAIL")
            self.assertIn("OPENROUTER_API_KEY", check["fix"])

    def test_doctor_reports_unavailable_ollama_api_with_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            ProviderManager(store.state_dir).add("ollama")
            node = shutil.which("node")

            with patch(
                "continuum.diagnostics.shutil.which",
                side_effect=lambda command: "ollama" if command == "ollama" else node,
            ), patch.object(
                ProviderManager, "models", side_effect=ProviderError("connection refused")
            ):
                results = run_doctor(store)

            check = next(item for item in results if item["name"] == "Ollama API localhost:11434")
            self.assertEqual(check["status"], "FAIL")
            self.assertIn("ollama serve", check["fix"])

    def test_doctor_reports_missing_ollama_installation_without_executing_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            ProviderManager(store.state_dir).add("ollama")
            node = shutil.which("node")

            with patch(
                "continuum.diagnostics.shutil.which",
                side_effect=lambda command: None if command == "ollama" else node,
            ), patch.object(ProviderManager, "models", side_effect=ProviderError("connection refused")):
                results = run_doctor(store)

            check = next(item for item in results if item["name"] == "Ollama installation")
            self.assertEqual(check["status"], "FAIL")
            self.assertIn("Install Ollama", check["fix"])

    def test_doctor_reports_passing_python_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            results = run_doctor(store)
            check = next(item for item in results if item["name"] == "Python import")
            self.assertEqual(check["status"], "PASS")

    def test_doctor_reports_project_initialization_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            results = run_doctor(store)
            check = next(item for item in results if item["name"] == "Project initialization")
            self.assertEqual(check["status"], "PASS")

    def test_doctor_reports_sqlite_read_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            results = run_doctor(store)
            check = next(item for item in results if item["name"] == "SQLite read/write")
            self.assertEqual(check["status"], "PASS")

    def test_doctor_reports_mcp_server_boot(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            results = run_doctor(store)
            check = next(item for item in results if item["name"] == "MCP server boot")
            self.assertEqual(check["status"], "PASS")

    def test_doctor_reports_daemon_stopped_when_no_pid(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            results = run_doctor(store)
            check = next(item for item in results if item["name"] == "Daemon process")
            self.assertIn(check["status"], ("FAIL", "INFO"))

    def test_doctor_reports_node_entrypoint_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            results = run_doctor(store)
            node_check = next(item for item in results if item["name"] == "Node entrypoint")
            self.assertIn(node_check["status"], ("PASS", "FAIL"))


if __name__ == "__main__":
    unittest.main()
