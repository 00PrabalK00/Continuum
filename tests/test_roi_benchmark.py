import json
import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr

from continuum.benchmark import capture_task, compare_captures, load_capture
from continuum.cli import main
from continuum.core import MemoryStore
from continuum.roi import roi_summary, task_metrics
from continuum.worktrees import WorktreeManager


class RoiBenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        subprocess.run(["git", "init"], cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Continuum Test"], cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.project, capture_output=True, check=True)
        (self.project / "app.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.project, capture_output=True, check=True)
        self.store = MemoryStore(self.project)
        self.store.initialize(1000, 0.8)
        self.manager = WorktreeManager(self.store)

    def tearDown(self):
        self.temporary.cleanup()

    def _ready_task(self):
        task = self.store.create_task("ROI task", "parallel")
        self.store.claim_files(task["task_id"], "claude", ["app.py"])
        record = self.manager.create(task["task_id"])
        (Path(record["path"]) / "app.py").write_text("value = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=record["path"], capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "change"], cwd=record["path"], capture_output=True, check=True)
        self.manager.record_tests(task["task_id"], True, "python -m unittest")
        self.manager.record_review(task["task_id"], True, "approved")
        self.store.event("model_ask", {"provider": "openrouter", "model": "openai/gpt-4o-mini", "summary": "plan"})
        return task

    def _run(self, argv):
        output = StringIO()
        with redirect_stdout(output):
            code = main(argv)
        return code, output.getvalue()

    def _run_err(self, argv):
        out = StringIO()
        err = StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_roi_summary_counts_quality_and_routing(self):
        self._ready_task()

        summary = roi_summary(self.store)

        self.assertEqual(summary["flight_records"], 1)
        self.assertEqual(summary["tests_passed"], 1)
        self.assertEqual(summary["merge_ready_tasks"], 1)
        self.assertTrue(summary["recommendations"])
        self.assertIn("openrouter", {item["provider"] for item in summary["provider_usage"]})

    def test_roi_cli_json(self):
        self._ready_task()
        code, text = self._run(["roi", "--project", str(self.project), "--json"])

        self.assertEqual(code, 0)
        payload = json.loads(text)
        self.assertIn("cost_per_accepted_change_tokens", payload)

    def test_benchmark_capture_and_compare(self):
        task = self._ready_task()
        capture = capture_task(self.store, task["task_id"], "with-continuum")
        baseline = {
            "label": "without-continuum",
            "metrics": {
                "tokens_used": capture["metrics"]["tokens_used"] + 100,
                "failed_attempts": 2,
                "files_touched_outside_scope": 1,
                "tests_run": 0,
                "context_resets": 1,
                "human_corrections": 1,
                "changed_files": capture["metrics"]["changed_files"],
                "merge_ready": False,
            },
        }

        comparison = compare_captures(baseline, capture)

        self.assertLess(comparison["delta"]["tokens_used"], 0)
        self.assertIn("improved", comparison["verdict"])

    def test_benchmark_cli_capture_and_compare(self):
        task = self._ready_task()
        without = self.project / "without.json"
        with_file = self.project / "with.json"
        without.write_text(
            json.dumps({
                "label": "without-continuum",
                "metrics": {
                    "tokens_used": 500,
                    "failed_attempts": 1,
                    "files_touched_outside_scope": 1,
                    "tests_run": 0,
                    "context_resets": 1,
                    "human_corrections": 1,
                    "changed_files": 2,
                    "merge_ready": False,
                },
            }),
            encoding="utf-8",
        )

        code, _ = self._run([
            "benchmark", "capture", task["task_id"], "--project", str(self.project),
            "--label", "with-continuum", "--output", str(with_file),
        ])
        self.assertEqual(code, 0)
        code, text = self._run([
            "benchmark", "compare", "--without", str(without), "--with", str(with_file), "--json",
        ])

        self.assertEqual(code, 0)
        self.assertIn("delta", json.loads(text))

    # --- Edge cases: empty/fresh store -------------------------------------

    def test_roi_summary_fresh_store_no_db(self):
        """roi_summary must not crash on a store that was never initialized."""
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "fresh"
            project.mkdir()
            store = MemoryStore(project)
            self.assertFalse(store.db_file.exists())

            summary = roi_summary(store)

            self.assertEqual(summary["tasks_total"], 0)
            self.assertEqual(summary["flight_records"], 0)
            self.assertEqual(summary["estimated_tokens"], 0)
            self.assertEqual(summary["cost_per_accepted_change_tokens"], 0)
            self.assertEqual(summary["provider_usage"], [])
            self.assertIsNone(summary["estimated_cost_usd"])
            self.assertTrue(summary["recommendations"])

    def test_roi_cli_json_fresh_store_exit_zero(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "fresh"
            project.mkdir()
            code, text = self._run(["roi", "--project", str(project), "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(text)
            self.assertEqual(payload["tasks_total"], 0)

    def test_flight_record_unknown_task_cli_exit_one(self):
        """A nonexistent task must surface a clean error, not a traceback."""
        code, _, err = self._run_err(
            ["flight-record", "T9999", "--project", str(self.project), "--json"]
        )
        self.assertEqual(code, 1)
        self.assertIn("Unknown task", err)

    # --- Edge cases: bare task (no worktree/gates/claims) ------------------

    def test_task_metrics_bare_task(self):
        task = self.store.create_task("bare task", "parallel")
        metrics = task_metrics(self.store, task["task_id"])
        self.assertEqual(metrics["changed_files"], 0)
        self.assertEqual(metrics["files_touched_outside_scope"], 0)
        self.assertEqual(metrics["tests_run"], 0)
        self.assertFalse(metrics["merge_ready"])
        self.assertFalse(metrics["review_recorded"])

    def test_benchmark_capture_bare_task_cli(self):
        task = self.store.create_task("bare task", "parallel")
        out = self.project / "bare.json"
        code, _ = self._run([
            "benchmark", "capture", task["task_id"], "--project", str(self.project),
            "--label", "bare", "--output", str(out),
        ])
        self.assertEqual(code, 0)
        capture = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(capture["label"], "bare")
        self.assertIn("metrics", capture)

    # --- Edge cases: provider aggregation ----------------------------------

    def test_provider_usage_model_provider_and_agent(self):
        self.store.event("model_ask", {"model": "gpt-4o"})  # no provider/agent -> skipped
        self.store.event("model_ask", {"provider": "openrouter", "model": "m1"})
        self.store.event("model_ask", {"agent": "claude"})  # agent, no model

        summary = roi_summary(self.store)
        usage = {item["provider"]: item for item in summary["provider_usage"]}

        self.assertNotIn("gpt-4o", usage)
        self.assertEqual(usage["openrouter"]["models"], ["m1"])
        self.assertEqual(usage["claude"]["models"], [])

    def test_provider_usage_ignores_non_dict_payload(self):
        """A non-dict event payload must not crash provider aggregation."""
        self.store.event("model_ask", {"provider": "openrouter"})
        # Write a raw event with a list payload directly into the events table.
        connection = self.store.connect()
        connection.execute(
            "INSERT INTO events(created_at, kind, payload) VALUES (?, ?, ?)",
            ("2026-01-01T00:00:00+00:00", "model_ask", json.dumps([1, 2, 3])),
        )
        connection.commit()
        connection.close()

        summary = roi_summary(self.store)
        self.assertIn("openrouter", {item["provider"] for item in summary["provider_usage"]})

    # --- Edge cases: compare_captures fallbacks & verdict branches ---------

    def test_compare_captures_flat_no_metrics_key(self):
        without = {"label": "base", "tokens_used": 100, "merge_ready": False}
        with_continuum = {"label": "ours", "tokens_used": 40, "merge_ready": True}
        comparison = compare_captures(without, with_continuum)
        self.assertEqual(comparison["delta"]["tokens_used"], -60)
        self.assertIn("improved", comparison["verdict"])

    def test_compare_captures_metrics_none_fallback(self):
        without = {"label": "base", "metrics": None, "tokens_used": 100}
        with_continuum = {"label": "ours", "metrics": None, "tokens_used": 40}
        comparison = compare_captures(without, with_continuum)
        self.assertEqual(comparison["delta"]["tokens_used"], -60)
        self.assertEqual(comparison["without_label"], "base")

    def test_compare_captures_verdict_tie(self):
        same = {"metrics": {key: 0 for key in (
            "tokens_used", "failed_attempts", "files_touched_outside_scope",
            "context_resets", "human_corrections", "tests_run", "changed_files",
        )}}
        comparison = compare_captures(same, dict(same))
        self.assertIn("Mixed or neutral", comparison["verdict"])

    def test_compare_captures_verdict_loss(self):
        without = {"metrics": {"tokens_used": 0}}
        with_continuum = {"metrics": {"tokens_used": 500}}
        comparison = compare_captures(without, with_continuum)
        self.assertIn("regressed", comparison["verdict"])

    def test_compare_captures_verdict_merge_ready_swing(self):
        without = {"metrics": {"merge_ready": False}}
        with_continuum = {"metrics": {"merge_ready": True}}
        comparison = compare_captures(without, with_continuum)
        self.assertIn("improved", comparison["verdict"])

    # --- Edge cases: benchmark compare CLI with bad inputs -----------------

    def test_benchmark_compare_missing_file_exit_one(self):
        good = self.project / "good.json"
        good.write_text(json.dumps({"metrics": {"tokens_used": 5}}), encoding="utf-8")
        code, _, err = self._run_err([
            "benchmark", "compare",
            "--without", str(self.project / "does-not-exist.json"),
            "--with", str(good), "--json",
        ])
        self.assertEqual(code, 1)
        self.assertIn("Cannot read benchmark capture", err)

    def test_benchmark_compare_malformed_json_exit_one(self):
        bad = self.project / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        good = self.project / "good.json"
        good.write_text(json.dumps({"metrics": {"tokens_used": 5}}), encoding="utf-8")
        code, _, err = self._run_err([
            "benchmark", "compare",
            "--without", str(bad), "--with", str(good), "--json",
        ])
        self.assertEqual(code, 1)
        self.assertIn("Invalid benchmark capture JSON", err)

    def test_load_capture_rejects_non_object(self):
        path = self.project / "arr.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_capture(path)


if __name__ == "__main__":
    unittest.main()
