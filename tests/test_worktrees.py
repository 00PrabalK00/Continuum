import subprocess
import tempfile
import unittest
from pathlib import Path

from continuum.core import MemoryStore
from continuum.worktrees import WorktreeError, WorktreeManager


class WorktreeManagerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        subprocess.run(["git", "init"], cwd=self.project, capture_output=True, check=True)
        # Persist identity to the repo config so operations that create commits
        # (e.g. `git merge`) work on CI runners with no global git identity.
        subprocess.run(["git", "config", "user.name", "Continuum Test"], cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.project, capture_output=True, check=True)
        (self.project / "app.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.project, capture_output=True, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Continuum Test", "-c", "user.email=test@example.invalid", "commit", "-m", "base"],
            cwd=self.project, capture_output=True, check=True,
        )
        self.store = MemoryStore(self.project)
        self.store.initialize(1000, 0.8)
        self.task = self.store.create_task("Isolated change", "parallel")
        self.manager = WorktreeManager(self.store)

    def tearDown(self):
        self.temporary.cleanup()

    def test_create_diff_and_discard_task_worktree(self):
        record = self.manager.create(self.task["task_id"])
        worktree = Path(record["path"])
        (worktree / "app.py").write_text("value = 2\n", encoding="utf-8")

        diff = self.manager.diff(self.task["task_id"])

        self.assertIn("app.py", diff["changed_files"])
        discarded = self.manager.discard(self.task["task_id"], force=True)
        self.assertEqual(discarded["status"], "DISCARDED")

    def test_schedule_creates_parallel_worktrees_with_context_packets(self):
        (self.project / "src").mkdir()
        (self.project / "tests").mkdir()
        schedule = self.manager.schedule(
            "split auth work",
            ["backend:claude:src", "tests:codex:tests"],
            ["tests:backend"],
        )

        self.assertEqual(schedule["schedule_id"], "P0001")
        self.assertEqual(len(schedule["lanes"]), 2)
        backend = next(lane for lane in schedule["lanes"] if lane["role"] == "backend")
        tests = next(lane for lane in schedule["lanes"] if lane["role"] == "tests")
        self.assertTrue(Path(backend["path"]).exists())
        self.assertTrue(Path(tests["context_path"]).exists())
        self.assertEqual(tests["dependencies"], ["backend"])
        self.assertEqual(self.store.get_task(tests["task_id"])["status"], "RUNNING")

    def test_schedule_rejects_overlapping_lane_ownership(self):
        with self.assertRaisesRegex(WorktreeError, "overlaps"):
            self.manager.schedule("unsafe split", ["backend:claude:src", "api:codex:src/api"])

    def test_dependency_blocks_merge_until_dependency_lane_is_done(self):
        (self.project / "src").mkdir()
        (self.project / "tests").mkdir()
        schedule = self.manager.schedule("split auth work", ["backend:claude:src", "tests:codex:tests"], ["tests:backend"])
        tests = next(lane for lane in schedule["lanes"] if lane["role"] == "tests")
        self.manager.record_tests(tests["task_id"], True, "tests pass")
        self.manager.record_review(tests["task_id"], True, "approved")

        with self.assertRaisesRegex(WorktreeError, "dependency"):
            self.manager.merge(tests["task_id"])

        status = self.manager.schedule_status(schedule["schedule_id"])
        tests_status = next(lane for lane in status["lanes"] if lane["role"] == "tests")
        self.assertFalse(tests_status["merge_ready"])

    def test_merge_requires_passing_test_gate(self):
        self.manager.create(self.task["task_id"])

        with self.assertRaisesRegex(WorktreeError, "test-result"):
            self.manager.merge(self.task["task_id"])

    def test_merge_requires_review_gate_after_tests_pass(self):
        self.manager.create(self.task["task_id"])
        self.manager.record_tests(self.task["task_id"], True, "tests pass")

        with self.assertRaisesRegex(WorktreeError, "review approval"):
            self.manager.merge(self.task["task_id"])

    def test_merge_rejects_commit_added_after_recorded_gates(self):
        record = self.manager.create(self.task["task_id"])
        worktree = Path(record["path"])
        self.manager.record_tests(self.task["task_id"], True, "tests pass")
        self.manager.record_review(self.task["task_id"], True, "approved")
        (worktree / "new.py").write_text("changed = True\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=worktree, capture_output=True, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Continuum Test", "-c", "user.email=test@example.invalid", "commit", "-m", "late change"],
            cwd=worktree, capture_output=True, check=True,
        )

        with self.assertRaisesRegex(WorktreeError, "changed after"):
            self.manager.merge(self.task["task_id"])

    def test_merge_removes_worktree_and_deletes_branch(self):
        record = self.manager.create(self.task["task_id"])
        worktree = Path(record["path"])
        branch = record["branch"]
        (worktree / "app.py").write_text("value = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=worktree, capture_output=True, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Continuum Test", "-c", "user.email=test@example.invalid", "commit", "-m", "change"],
            cwd=worktree, capture_output=True, check=True,
        )
        # Commit Continuum-generated scaffolding so the main tree is clean for the merge gate.
        subprocess.run(["git", "add", "-A"], cwd=self.project, capture_output=True, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Continuum Test", "-c", "user.email=test@example.invalid", "commit", "-m", "scaffolding"],
            cwd=self.project, capture_output=True, check=True,
        )
        self.manager.record_tests(self.task["task_id"], True, "tests pass")
        self.manager.record_review(self.task["task_id"], True, "approved")

        merged = self.manager.merge(self.task["task_id"])

        self.assertEqual(merged["status"], "MERGED")
        self.assertNotIn("cleanup_warning", merged)
        self.assertFalse(worktree.exists())
        worktrees = subprocess.run(
            ["git", "worktree", "list"], cwd=self.project, capture_output=True, text=True, check=True
        ).stdout
        self.assertNotIn(str(worktree), worktrees)
        branches = subprocess.run(
            ["git", "branch", "--list", branch], cwd=self.project, capture_output=True, text=True, check=True
        ).stdout
        self.assertEqual(branches.strip(), "")

    def test_merge_rejects_uncommitted_changes_after_recorded_gates(self):
        record = self.manager.create(self.task["task_id"])
        worktree = Path(record["path"])
        self.manager.record_tests(self.task["task_id"], True, "tests pass")
        self.manager.record_review(self.task["task_id"], True, "approved")
        (worktree / "app.py").write_text("value = 3\n", encoding="utf-8")

        with self.assertRaisesRegex(WorktreeError, "uncommitted changes"):
            self.manager.merge(self.task["task_id"])


if __name__ == "__main__":
    unittest.main()
