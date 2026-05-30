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
            self.assertIn("Review completed workflow", (store.state_dir / "latest_handoff.md").read_text(encoding="utf-8"))

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
            self.assertIn("Inspect failed workflow", (store.state_dir / "latest_handoff.md").read_text(encoding="utf-8"))

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

    def test_retry_resumes_from_failed_step_without_recreating_earlier_tasks(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            TeamManager(store).init("local_only")
            calls = {"count": 0}

            def flaky(provider, prompt):
                calls["count"] += 1
                if calls["count"] == 2:
                    raise RuntimeError("provider timeout")
                return f"{provider} ok"

            with self.assertRaisesRegex(OrchestrationError, "provider timeout"):
                Orchestrator(store, runner=flaky).execute("local_only", "plan work")

            failed = store.list_workflows(1)[0]
            self.assertEqual(failed["status"], "FAILED")
            self.assertEqual(failed["steps"][0]["status"], "DONE")
            self.assertEqual(failed["steps"][1]["status"], "FAILED")
            step0_task = failed["steps"][0]["task_id"]
            step1_task = failed["steps"][1]["task_id"]
            self.assertEqual(store.get_task(step0_task)["status"], "DONE")
            task_count_before = len(store.list_tasks(limit=50))

            resumed = Orchestrator(store, runner=lambda provider, prompt: f"{provider} ok").retry(
                failed["workflow_id"], execute=True
            )

            self.assertEqual(resumed["status"], "DONE")
            # Earlier completed step's task is preserved (same id, still DONE).
            self.assertEqual(resumed["steps"][0]["task_id"], step0_task)
            self.assertEqual(resumed["steps"][0]["status"], "DONE")
            self.assertEqual(resumed["steps"][1]["task_id"], step1_task)
            self.assertEqual(resumed["steps"][1]["status"], "DONE")
            # No new tasks were created on retry.
            self.assertEqual(len(store.list_tasks(limit=50)), task_count_before)
            retry_events = [event for event in store.recent_events(80) if event["kind"] == "workflow_retry"]
            self.assertEqual(len(retry_events), 1)
            self.assertEqual(retry_events[0]["payload"]["resume_step"], 2)

    def test_retry_without_execute_replans_remaining_steps(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            TeamManager(store).init("local_only")
            calls = {"count": 0}

            def flaky(provider, prompt):
                calls["count"] += 1
                if calls["count"] == 2:
                    raise RuntimeError("provider timeout")
                return f"{provider} ok"

            with self.assertRaisesRegex(OrchestrationError, "provider timeout"):
                Orchestrator(store, runner=flaky).execute("local_only", "plan work")
            failed = store.list_workflows(1)[0]
            task_count_before = len(store.list_tasks(limit=50))

            replanned = Orchestrator(store, runner=lambda provider, prompt: "noop").retry(
                failed["workflow_id"], execute=False
            )

            self.assertEqual(replanned["status"], "PLANNED")
            self.assertEqual(replanned["steps"][0]["status"], "DONE")
            self.assertEqual(replanned["steps"][1]["status"], "PLANNED")
            self.assertEqual(len(store.list_tasks(limit=50)), task_count_before)

    def test_retry_rejects_completed_workflow(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            TeamManager(store).init("local_only")
            workflow = Orchestrator(store, runner=lambda provider, prompt: "ok").execute("local_only", "plan work")

            with self.assertRaisesRegex(OrchestrationError, "already completed"):
                Orchestrator(store, runner=lambda provider, prompt: "ok").retry(workflow["workflow_id"], execute=True)


if __name__ == "__main__":
    unittest.main()
