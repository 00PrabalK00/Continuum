import json
import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from contextlib import redirect_stdout

from continuum.benchmark import capture_task, compare_captures
from continuum.cli import main
from continuum.core import MemoryStore
from continuum.roi import roi_summary
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


if __name__ == "__main__":
    unittest.main()
