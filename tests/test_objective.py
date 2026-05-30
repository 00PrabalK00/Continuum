import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from continuum.cli import main
from continuum.core import MemoryStore
from continuum.objective import ObjectiveError, plan_objective


class ObjectiveTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        subprocess.run(["git", "init"], cwd=self.project, capture_output=True, check=True)
        # CI has no global git identity; persist it so worktree/merge commits work.
        subprocess.run(["git", "config", "user.name", "Continuum Test"], cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.project, capture_output=True, check=True)
        (self.project / "src").mkdir()
        (self.project / "tests").mkdir()
        (self.project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
        (self.project / "tests" / "test_app.py").write_text("def test():\n    pass\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.project, capture_output=True, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Continuum Test", "-c", "user.email=test@example.invalid", "commit", "-m", "base"],
            cwd=self.project, capture_output=True, check=True,
        )
        self.store = MemoryStore(self.project)
        self.store.initialize(1000, 0.8)

    def tearDown(self):
        self.temporary.cleanup()

    def test_plan_mode_creates_tasks_and_next_commands_without_worktrees(self):
        result = plan_objective(
            self.store,
            "Add billing",
            agent_specs=["backend=claude", "tests=codex"],
            mode="plan",
        )
        self.assertEqual(result["mode"], "plan")
        self.assertFalse(result["scheduled"])
        self.assertIsNone(result["schedule_id"])
        self.assertEqual(len(result["tasks"]), 2)
        # Tasks exist but no worktree was created in plan mode.
        for task_id in result["tasks"]:
            self.assertIsNotNone(self.store.get_task(task_id))
        self.assertFalse((self.store.state_dir / "worktrees.json").exists())
        # Next commands tell the user how to drive each lane and end with pr-packet.
        joined = "\n".join(result["next_commands"])
        self.assertIn("continuum resume claude", joined)
        self.assertIn("continuum pr-packet", joined)
        for lane in result["lanes"]:
            self.assertNotIn("branch", lane)

    def test_schedule_mode_creates_lanes_branches_worktrees_packets(self):
        result = plan_objective(
            self.store,
            "Add billing",
            agent_specs=["backend=claude", "tests=codex"],
            path_specs=["backend=src", "tests=tests"],
            depends_on=["tests:backend"],
            mode="schedule",
        )
        self.assertTrue(result["scheduled"])
        self.assertEqual(result["schedule_id"], "P0001")
        backend = next(lane for lane in result["lanes"] if lane["role"] == "backend")
        tests = next(lane for lane in result["lanes"] if lane["role"] == "tests")
        self.assertTrue(Path(backend["worktree"]).exists())
        self.assertTrue(Path(tests["context_path"]).exists())
        self.assertEqual(tests["depends_on"], ["backend"])
        # Dependency ordering puts backend before tests in the timeline.
        self.assertEqual(result["timeline"], ["backend", "tests"])
        # Scheduled lanes emit worktree resume + merge commands.
        joined = "\n".join(result["next_commands"])
        self.assertIn(f"continuum worktree resume {backend['task_id']} claude", joined)
        self.assertIn(f"continuum worktree merge {tests['task_id']}", joined)

    def test_overlapping_paths_rejected(self):
        with self.assertRaises(ObjectiveError):
            plan_objective(
                self.store,
                "Unsafe split",
                agent_specs=["backend=claude", "api=codex"],
                path_specs=["backend=src", "api=src/api"],
                mode="schedule",
            )

    def test_tests_and_review_policy_recorded_in_next_commands(self):
        result = plan_objective(
            self.store,
            "Add billing",
            agent_specs=["backend=claude"],
            path_specs=["backend=src"],
            tests="pytest -q",
            review_policy="none",
            mode="schedule",
        )
        joined = "\n".join(result["next_commands"])
        self.assertIn('--note "pytest -q"', joined)
        # review-policy none drops the review gate command.
        self.assertNotIn("worktree review", joined)

    def test_path_without_lane_in_schedule_creates_task_only(self):
        result = plan_objective(
            self.store,
            "Add billing",
            agent_specs=["backend=claude", "docs=gemini"],
            path_specs=["backend=src"],
            mode="schedule",
        )
        docs = next(lane for lane in result["lanes"] if lane["role"] == "docs")
        # docs has no path: it gets a task but no worktree.
        self.assertIn("task_id", docs)
        self.assertNotIn("branch", docs)
        self.assertTrue(any("No worktree created" in note for note in result["notes"]))

    def test_json_shape(self):
        argv = [
            "objective", "Add billing", "--project", str(self.project),
            "--agent", "backend=claude", "--agent", "tests=codex",
            "--path", "backend=src", "--path", "tests=tests",
            "--depends-on", "tests:backend", "--mode", "schedule", "--json",
        ]
        import io
        import contextlib

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        for key in ("goal", "task_type", "mode", "lanes", "timeline", "tasks", "next_commands", "scheduled"):
            self.assertIn(key, payload)
        self.assertTrue(payload["scheduled"])
        self.assertEqual(payload["timeline"], ["backend", "tests"])
        self.assertEqual(len(payload["lanes"]), 2)

    def test_execute_with_disabled_provider_refuses_gracefully(self):
        argv = [
            "objective", "Add billing", "--project", str(self.project),
            "--agent", "backend=claude", "--path", "backend=src",
            "--mode", "schedule", "--execute",
        ]
        with self.assertRaises(SystemExit) as ctx:
            main(argv)
        message = str(ctx.exception.code)
        self.assertIn("Objective stopped", message)
        self.assertIn("enabled", message)

    def test_unknown_agent_rejected(self):
        with self.assertRaises(ObjectiveError):
            plan_objective(self.store, "Add billing", agent_specs=["backend=robot"], mode="plan")


if __name__ == "__main__":
    unittest.main()
