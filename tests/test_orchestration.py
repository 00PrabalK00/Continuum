import tempfile
import unittest
import subprocess
from pathlib import Path

from continuum.core import MemoryStore
from continuum.orchestration import OrchestrationError, Orchestrator
from continuum.teams import TeamManager


class OrchestratorTest(unittest.TestCase):
    def test_model_only_team_executes_steps_sequentially_and_passes_messages(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            TeamManager(store).init("local_only")
            calls = []

            def runner(provider, prompt):
                calls.append((provider, prompt))
                return f"{provider} result"

            workflow = Orchestrator(store, runner=runner).execute("local_only", "plan new feature")

            self.assertEqual(workflow["status"], "DONE")
            self.assertEqual([call[0] for call in calls], ["ollama", "ollama"])
            self.assertIn("ollama result", calls[1][1])
            self.assertEqual(len(store.messages(workflow_ref=workflow["workflow_id"])), 2)

    def test_writer_execution_requires_explicit_claimed_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            TeamManager(store).init("fast_bugfix")

            with self.assertRaisesRegex(OrchestrationError, "--allow-file"):
                Orchestrator(store, runner=lambda provider, prompt: "done").execute(
                    "fast_bugfix", "fix login crash"
                )

    def test_writer_with_explicit_path_can_complete_under_fake_adapter(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            TeamManager(store).init("fast_bugfix")

            workflow = Orchestrator(store, runner=lambda provider, prompt: "bounded result").execute(
                "fast_bugfix", "fix login crash", allowed_files=["src/login.py"]
            )

            self.assertEqual(workflow["status"], "DONE")

    def test_unclaimed_write_fails_and_preserves_provider_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
            store = MemoryStore(project)
            store.initialize(1000, 0.8)
            TeamManager(store).init("fast_bugfix")
            subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Continuum Test", "-c", "user.email=test@example.invalid", "commit", "-m", "baseline"],
                cwd=project, capture_output=True, check=True,
            )

            def writer(provider, prompt):
                (project / "outside.py").write_text("changed\n", encoding="utf-8")
                return "agent reported implementation complete"

            with self.assertRaisesRegex(OrchestrationError, "outside.py"):
                Orchestrator(store, runner=writer).execute(
                    "fast_bugfix", "fix login crash", allowed_files=["src/login.py"]
                )

            workflow = store.list_workflows(1)[0]
            self.assertIn("agent reported implementation complete", workflow["steps"][0]["output"])

    def test_provider_failure_after_unclaimed_write_reports_both_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
            store = MemoryStore(project)
            store.initialize(1000, 0.8)
            TeamManager(store).init("fast_bugfix")
            subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Continuum Test", "-c", "user.email=test@example.invalid", "commit", "-m", "baseline"],
                cwd=project, capture_output=True, check=True,
            )

            def broken_writer(provider, prompt):
                (project / "file with space.py").write_text("changed\n", encoding="utf-8")
                raise RuntimeError("provider connection dropped")

            with self.assertRaisesRegex(OrchestrationError, "file with space.py"):
                Orchestrator(store, runner=broken_writer).execute(
                    "fast_bugfix", "fix login crash", allowed_files=["src/login.py"]
                )

            workflow = store.list_workflows(1)[0]
            self.assertIn("provider connection dropped", workflow["steps"][0]["output"])
            self.assertEqual(workflow["status"], "FAILED")

    def test_unexpected_model_step_failure_marks_workflow_failed(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            TeamManager(store).init("local_only")

            with self.assertRaisesRegex(OrchestrationError, "boom"):
                Orchestrator(store, runner=lambda provider, prompt: (_ for _ in ()).throw(KeyError("boom"))).execute(
                    "local_only", "plan work"
                )

            self.assertEqual(store.list_workflows(1)[0]["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
