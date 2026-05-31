"""End-to-end trust demo flow integration test.

This test proves the full advertised v0.9.0 pipeline works as ONE chained flow on
a real temporary git project, driving the public API (MemoryStore + managers) and
the CLI surface (`continuum.cli.main`) exactly as a user would:

    continuum init
      -> objective (--mode schedule: tasks + worktree lanes + owned paths)
      -> claim files (done by the scheduler)
      -> worktree create (done by the scheduler)
      -> real change + commit inside the lane worktree
      -> record_tests (PASS) + record_review (APPROVED)
      -> evidence (changed files + PASS/APPROVED)
      -> pr-packet
      -> flight-record (final_status == merge_ready)
      -> roi (flight_records >= 1, merge_ready_tasks >= 1)
      -> benchmark capture + compare (verdict "improved")

It also asserts every CLI command in the flow exits 0 on the populated project and
exits 1 (no traceback) on a bogus task id.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from continuum.benchmark import compare_captures, load_capture
from continuum.cli import main
from continuum.core import MemoryStore
from continuum.evidence import gather_evidence
from continuum.flight import gather_flight_record
from continuum.roi import roi_summary
from continuum.worktrees import WorktreeManager


class DemoFlowEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        # Real git project. Git identity MUST be persisted in the repo config because
        # CI runners have no global identity and the worktree commit + final merge
        # both need an author.
        subprocess.run(["git", "init"], cwd=self.project, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.name", "Continuum Test"],
            cwd=self.project, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=self.project, capture_output=True, check=True,
        )
        (self.project / "src").mkdir()
        (self.project / "tests").mkdir()
        (self.project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
        (self.project / "tests" / "test_app.py").write_text(
            "def test():\n    pass\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "."], cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.project, capture_output=True, check=True)

    def tearDown(self):
        self.temporary.cleanup()

    # -- helpers ---------------------------------------------------------------

    def _run(self, argv):
        """Run a CLI command with --project pinned to the temp project.

        Returns (exit_code, stdout). SystemExit raised by argument/usage errors is
        surfaced as a non-zero exit code with its message captured, so tests can
        assert graceful failures without a traceback.
        """
        full = list(argv)
        if "--project" not in full:
            full += ["--project", str(self.project)]
        output = StringIO()
        try:
            with redirect_stdout(output):
                code = main(full)
        except SystemExit as error:  # graceful refusals raise SystemExit(message)
            code = error.code if isinstance(error.code, int) else 1
        return code, output.getvalue()

    def _commit_change_in_worktree(self, worktree_path: str, relative: str, content: str):
        target = Path(worktree_path) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=worktree_path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "feature change"], cwd=worktree_path, capture_output=True, check=True)

    # -- the chained flow ------------------------------------------------------

    def test_full_trust_demo_flow_end_to_end(self):
        # 1. continuum init (CLI, on the real temp project).
        code, _ = self._run(["init"])
        self.assertEqual(code, 0)

        store = MemoryStore(self.project)

        # 2. objective --mode schedule: one goal -> tasks + worktree lanes that own
        #    isolated paths. This is the "objective (creates tasks + worktree lanes)"
        #    + "claim files" + "worktree create" steps in one command.
        # Lanes own exact file paths. NOTE: the out-of-scope risk heuristic in
        # evidence._risks compares changed files against claimed paths by exact
        # match, so owning a directory (e.g. `src`) does NOT cover a nested change
        # like `src/app.py`. The demo therefore owns the precise files each lane
        # edits, which is also how a real reviewer wants scope expressed.
        code, text = self._run([
            "objective", "Add billing",
            "--agent", "backend=claude", "--agent", "tests=codex",
            "--path", "backend=src/app.py", "--path", "tests=tests/test_app.py",
            "--depends-on", "tests:backend",
            "--mode", "schedule", "--json",
        ])
        self.assertEqual(code, 0, "objective --mode schedule should exit 0")
        plan = json.loads(text)
        self.assertTrue(plan["scheduled"])
        self.assertEqual(plan["schedule_id"], "P0001")
        self.assertEqual(len(plan["tasks"]), 2)
        # Lanes carry owned paths and worktree branches (assertion 1).
        backend = next(lane for lane in plan["lanes"] if lane["role"] == "backend")
        tests = next(lane for lane in plan["lanes"] if lane["role"] == "tests")
        self.assertEqual(backend["paths"], ["src/app.py"])
        self.assertEqual(tests["paths"], ["tests/test_app.py"])
        for lane in (backend, tests):
            self.assertIn("task_id", lane)
            self.assertIn("branch", lane)
            self.assertTrue(Path(lane["worktree"]).exists())
            self.assertTrue(Path(lane["context_path"]).exists())

        backend_task = backend["task_id"]

        # The scheduler claimed the lane's owned paths for us (assertion 1: claims).
        claimed = {claim["path"] for claim in store.list_claims()}
        self.assertIn("src/app.py", claimed)

        # 3. Make a real change + commit inside the backend lane's isolated worktree.
        self._commit_change_in_worktree(backend["worktree"], "src/app.py", "value = 2\n")

        # 4. Record PASS test gate + APPROVED review gate against that HEAD via the
        #    public manager (mirrors `continuum worktree test-result/review`).
        manager = WorktreeManager(store)
        manager.record_tests(backend_task, True, "python -m unittest")
        manager.record_review(backend_task, True, "Looks correct; scoped to src.")

        # 5. evidence: shows the changed files and the PASS/APPROVED gates (assertion 2).
        evidence = gather_evidence(store, backend_task)
        self.assertIn("src/app.py", evidence["changed_files"])
        self.assertEqual(evidence["test_gate"]["result"], "PASS")
        self.assertEqual(evidence["review_gate"]["result"], "APPROVED")
        # No risks once both gates are recorded against the current HEAD and the
        # only changed file is the claimed one.
        self.assertEqual(evidence["risks"], [], f"unexpected risks: {evidence['risks']}")

        # 6. flight-record: final_status must be merge_ready (gates PASS/APPROVED, not
        #    yet merged, no risks) (assertion 3).
        flight = gather_flight_record(store, backend_task)
        self.assertEqual(flight["final_status"], "merge_ready")
        self.assertIn("src/app.py", flight["files_touched"])

        # 7. roi: at least one flight record and one merge-ready task (assertion 4).
        roi = roi_summary(store)
        self.assertGreaterEqual(roi["flight_records"], 1)
        self.assertGreaterEqual(roi["merge_ready_tasks"], 1)

        # 8. benchmark capture (CLI) then compare against a synthetic WORSE baseline.
        #    The with-Continuum run wins on every quality metric, so the verdict must
        #    be the advertised "improved" line (assertion 5).
        with_file = self.project / "with.json"
        without_file = self.project / "without.json"
        code, _ = self._run([
            "benchmark", "capture", backend_task,
            "--label", "with-continuum", "--output", str(with_file),
        ])
        self.assertEqual(code, 0)
        with_capture = load_capture(with_file)
        worse_metrics = dict(with_capture["metrics"])
        worse_metrics.update({
            "tokens_used": worse_metrics.get("tokens_used", 0) + 1000,
            "failed_attempts": 3,
            "files_touched_outside_scope": 2,
            "context_resets": 2,
            "human_corrections": 2,
            "merge_ready": False,
        })
        without_file.write_text(
            json.dumps({"label": "without-continuum", "metrics": worse_metrics}),
            encoding="utf-8",
        )
        comparison = compare_captures(load_capture(without_file), with_capture)
        self.assertEqual(comparison["verdict"], "Continuum improved the measured run.")

        # The CLI compare path renders the same verdict and exits 0.
        code, compare_text = self._run([
            "benchmark", "compare",
            "--without", str(without_file), "--with", str(with_file),
        ])
        self.assertEqual(code, 0)
        self.assertIn("Continuum improved the measured run.", compare_text)

    # -- CLI surface: exit 0 on populated project, exit 1 on bogus task --------

    def test_cli_commands_exit_zero_on_populated_project(self):
        self._run(["init"])
        store = MemoryStore(self.project)

        # objective (CLI) exits 0 and yields a worktree-backed task to inspect.
        code, text = self._run([
            "objective", "Add billing",
            "--agent", "backend=claude", "--path", "backend=src/app.py",
            "--mode", "schedule", "--json",
        ])
        self.assertEqual(code, 0)
        plan = json.loads(text)
        backend = next(lane for lane in plan["lanes"] if lane["role"] == "backend")
        task_id = backend["task_id"]

        self._commit_change_in_worktree(backend["worktree"], "src/app.py", "value = 9\n")
        manager = WorktreeManager(store)
        manager.record_tests(task_id, True, "python -m unittest")
        manager.record_review(task_id, True, "approved")

        packet_path = self.project / "packet.md"
        capture_path = self.project / "cap.json"
        baseline_path = self.project / "baseline.json"

        ok_commands = [
            ["objective", "Add logging", "--agent", "docs=gemini", "--mode", "plan"],
            ["evidence", task_id],
            ["evidence", task_id, "--json"],
            ["pr-packet", task_id],
            ["pr-packet", task_id, "--output", str(packet_path)],
            ["flight-record", task_id],
            ["flight-record", task_id, "--json"],
            ["roi"],
            ["roi", "--json"],
            ["benchmark", "capture", task_id, "--output", str(capture_path)],
        ]
        for argv in ok_commands:
            code, _ = self._run(argv)
            self.assertEqual(code, 0, f"expected exit 0 for: {' '.join(argv)}")

        # benchmark compare needs two capture files; build a worse baseline.
        capture = load_capture(capture_path)
        worse = dict(capture["metrics"])
        worse.update({
            "tokens_used": worse.get("tokens_used", 0) + 1000,
            "failed_attempts": 5,
            "files_touched_outside_scope": 3,
            "context_resets": 3,
            "human_corrections": 3,
            "merge_ready": False,
        })
        baseline_path.write_text(
            json.dumps({"label": "without-continuum", "metrics": worse}), encoding="utf-8"
        )
        code, _ = self._run([
            "benchmark", "compare", "--without", str(baseline_path), "--with", str(capture_path),
        ])
        self.assertEqual(code, 0)

    def test_cli_commands_exit_one_no_traceback_on_bogus_task(self):
        self._run(["init"])
        bogus = "T9999"
        bogus_commands = [
            ["evidence", bogus],
            ["evidence", bogus, "--json"],
            ["pr-packet", bogus],
            ["flight-record", bogus],
            ["flight-record", bogus, "--json"],
            ["benchmark", "capture", bogus],
        ]
        for argv in bogus_commands:
            code, _ = self._run(argv)
            self.assertEqual(code, 1, f"expected graceful exit 1 for: {' '.join(argv)}")


if __name__ == "__main__":
    unittest.main()
