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
