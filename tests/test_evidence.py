import subprocess
import tempfile
import unittest
from pathlib import Path

from continuum.cli import main
from continuum.core import MemoryStore
from continuum.evidence import EvidenceError, gather_evidence, render_packet
from continuum.worktrees import WorktreeManager


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        subprocess.run(["git", "init"], cwd=self.project, capture_output=True, check=True)
        # Persist identity to the repo config so commit/merge operations work on CI
        # runners that have no global git identity.
        subprocess.run(["git", "config", "user.name", "Continuum Test"], cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.project, capture_output=True, check=True)
        (self.project / "app.py").write_text("value = 1\n", encoding="utf-8")
        (self.project / "other.py").write_text("other = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.project, capture_output=True, check=True)
        self.store = MemoryStore(self.project)
        self.store.initialize(1000, 0.8)
        self.manager = WorktreeManager(self.store)

    def tearDown(self):
        self.temporary.cleanup()

    def _commit_worktree(self, record, message="change"):
        path = Path(record["path"])
        subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", message], cwd=path, capture_output=True, check=True)

    def _full_evidence_task(self):
        task = self.store.create_task("Isolated change", "parallel")
        self.store.claim_files(task["task_id"], "claude", ["app.py"])
        record = self.manager.create(task["task_id"])
        worktree = Path(record["path"])
        (worktree / "app.py").write_text("value = 2\n", encoding="utf-8")
        self._commit_worktree(record)
        self.manager.record_tests(task["task_id"], True, "pytest -q")
        self.manager.record_review(task["task_id"], True, "looks good")
        return task

    def test_evidence_for_task_with_worktree_and_both_gates(self):
        task = self._full_evidence_task()
        evidence = gather_evidence(self.store, task["task_id"])

        self.assertEqual(evidence["status"], self.store.get_task(task["task_id"])["status"])
        self.assertEqual(evidence["claimed_files"], ["app.py"])
        self.assertIn("app.py", evidence["changed_files"])
        self.assertEqual(evidence["test_gate"]["result"], "PASS")
        self.assertEqual(evidence["review_gate"]["result"], "APPROVED")
        self.assertTrue(evidence["test_gate"]["sha"])
        self.assertTrue(evidence["review_gate"]["sha"])
        # Both gates recorded against current HEAD and only the claimed file changed,
        # so no deterministic risk should fire.
        self.assertEqual(evidence["risks"], [])
        self.assertIn("merge", evidence["next_action"])

    def test_out_of_scope_change_is_flagged_as_risk(self):
        task = self.store.create_task("Scoped change", "parallel")
        self.store.claim_files(task["task_id"], "claude", ["app.py"])
        record = self.manager.create(task["task_id"])
        worktree = Path(record["path"])
        (worktree / "app.py").write_text("value = 2\n", encoding="utf-8")
        # Edit a file that was never claimed -> out-of-scope.
        (worktree / "other.py").write_text("other = 2\n", encoding="utf-8")
        self._commit_worktree(record)
        self.manager.record_tests(task["task_id"], True, "pytest -q")
        self.manager.record_review(task["task_id"], True, "ok")

        evidence = gather_evidence(self.store, task["task_id"])

        self.assertTrue(any("Out-of-scope edit: `other.py`" in risk for risk in evidence["risks"]))

    def test_changed_after_gate_is_flagged(self):
        task = self._full_evidence_task()
        # Add a new commit after both gates were recorded.
        record = self.manager.record(task["task_id"])
        (Path(record["path"]) / "app.py").write_text("value = 3\n", encoding="utf-8")
        self._commit_worktree(record, "after gate")

        evidence = gather_evidence(self.store, task["task_id"])

        self.assertTrue(any("test gate" in risk for risk in evidence["risks"]))
        self.assertTrue(any("review gate" in risk for risk in evidence["risks"]))

    def test_unknown_task_raises(self):
        with self.assertRaises(EvidenceError):
            gather_evidence(self.store, "T9999")

    def test_evidence_json_shape(self):
        task = self._full_evidence_task()
        evidence = gather_evidence(self.store, task["task_id"])
        for key in (
            "task_id",
            "title",
            "status",
            "agent",
            "mode",
            "claimed_files",
            "changed_files",
            "test_gate",
            "review_gate",
            "risks",
            "next_action",
            "events",
            "contributions",
        ):
            self.assertIn(key, evidence)
        self.assertIsInstance(evidence["claimed_files"], list)
        self.assertIsInstance(evidence["risks"], list)
        self.assertEqual(set(evidence["test_gate"]), {"result", "note", "sha"})

    def test_evidence_without_worktree_reports_no_worktree(self):
        task = self.store.create_task("No worktree task", "sequential")
        evidence = gather_evidence(self.store, task["task_id"])
        self.assertEqual(evidence["worktree_note"], "no worktree")
        self.assertIsNone(evidence["worktree"])
        self.assertEqual(evidence["changed_files"], [])
        self.assertIn("worktree create", evidence["next_action"])

    def test_pr_packet_contains_test_review_and_rollback(self):
        task = self._full_evidence_task()
        evidence = gather_evidence(self.store, task["task_id"])
        markdown = render_packet(evidence)

        self.assertIn("## Test Evidence", markdown)
        self.assertIn("PASS", markdown)
        self.assertIn("## Review Evidence", markdown)
        self.assertIn("APPROVED", markdown)
        self.assertIn("## Rollback", markdown)
        self.assertIn(evidence["branch"], markdown)
        self.assertIn("## Agent Contributions", markdown)

    def test_evidence_cli_json_exit_zero(self):
        task = self._full_evidence_task()
        code = main(["evidence", task["task_id"], "--project", str(self.project), "--json"])
        self.assertEqual(code, 0)

    def test_pr_packet_cli_writes_output_file(self):
        task = self._full_evidence_task()
        out = self.project / "packet.md"
        code = main(["pr-packet", task["task_id"], "--project", str(self.project), "--output", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())
        self.assertIn("## Rollback", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
