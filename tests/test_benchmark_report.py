"""The report generator exists so published numbers cannot drift from the run.

These tests mostly check that it refuses to state anything the results file does
not contain, since a generator that quietly invents a figure is worse than a
hand-written table, which at least nobody trusts.
"""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))

import report as reporter  # noqa: E402


def run(**overrides) -> dict:
    base = {
        "schema": 2,
        "trials": 30,
        "probe_kinds": {"class": "distractor", "file": "recall", "owner": "unanswerable"},
        "context_tokens": {"raw_history": 1000, "deep": 600, "normal": 400, "compact": 100},
        "fidelity": {
            "claude/injected": {
                "runs": 30, "completed": 30, "accuracy_pct": 96.7,
                "accuracy_ci95": [92.0, 99.3], "seconds_mean": 5.4,
                "per_probe_pct": {"class": 100.0, "file": 100.0, "owner": 90.0},
            },
            "claude/files_only": {
                "runs": 30, "completed": 30, "accuracy_pct": 94.0,
                "accuracy_ci95": [88.0, 98.0], "seconds_mean": 29.1,
                "per_probe_pct": {},
            },
            "claude/no_memory": {
                "runs": 30, "completed": 28, "accuracy_pct": 17.3,
                "accuracy_ci95": [14.7, 19.3], "seconds_mean": 19.8,
                "per_probe_pct": {},
            },
        },
    }
    base.update(overrides)
    return base


class NoInventedNumbersTest(unittest.TestCase):
    def test_every_percentage_printed_is_in_the_results(self):
        report = run()
        text = reporter.render(report)
        allowed = {"95", "100", "30"}  # prose: the interval level, the axis, the trial count
        for entry in report["fidelity"].values():
            allowed.add(f"{entry['accuracy_pct']:.0f}")
            allowed.update(f"{value:.0f}" for value in entry["accuracy_ci95"])
            allowed.update(f"{value:.0f}" for value in entry["per_probe_pct"].values())
        sizes = report["context_tokens"]
        for mode in ("deep", "normal", "compact"):
            allowed.add(f"{100 - 100 * sizes[mode] / sizes['raw_history']:.0f}")
        printed = set(re.findall(r"(\d+)%", text))
        self.assertTrue(
            printed <= allowed,
            f"printed percentages not traceable to the results: {sorted(printed - allowed)}",
        )

    def test_an_unmeasured_cell_says_so_rather_than_showing_zero(self):
        report = run()
        report["fidelity"]["codex/injected"] = {"runs": 30, "completed": 0}
        text = reporter.render(report)
        self.assertIn("not measured", text)
        # A cell with no completed trials must not appear as a real result.
        self.assertNotIn("| 0% ", text)

    def test_sections_with_no_data_are_omitted_entirely(self):
        text = reporter.render(run())
        for absent in ("## Delegation", "## Categories", "## Which source"):
            self.assertNotIn(absent, text)

    def test_sections_appear_once_their_data_does(self):
        report = run(
            conflict={"claude": {"trials": 30, "answered_from_injected": 30, "answered_from_disk": 0}},
            categories=[{"agent": "claude", "category": "temporal", "passed": 29, "trials": 30}],
            delegation={"claude": {"completed": 30, "runs": 30, "seconds_mean": 7.3}},
        )
        text = reporter.render(report)
        self.assertIn("## Delegation", text)
        self.assertIn("## Categories", text)
        self.assertIn("29/30", text)
        self.assertIn("30/30", text)


class ContentTest(unittest.TestCase):
    def test_the_files_only_arm_is_in_the_table_not_hidden_in_prose(self):
        text = reporter.render(run())
        accuracy = text.split("## Accuracy")[1].split("##")[0]
        self.assertIn("reads `.continuum/` itself", accuracy)
        self.assertIn("94%", accuracy)

    def test_the_probe_kinds_are_explained(self):
        text = reporter.render(run())
        for kind in ("distractor", "recall", "unanswerable"):
            self.assertIn(kind, text)

    def test_the_known_faults_are_kept_on_the_page(self):
        text = reporter.render(run())
        self.assertIn("Faults this benchmark has had", text)
        self.assertIn("withdrawn", text)

    def test_the_reproduce_command_matches_the_trial_count(self):
        text = reporter.render(run(trials=7))
        self.assertIn("--trials 7", text)

    def test_completion_is_reported_separately_from_accuracy(self):
        text = reporter.render(run())
        self.assertIn("28/30", text)

    def test_the_headline_uses_the_weakest_agent_not_the_best(self):
        report = run()
        report["fidelity"]["codex/injected"] = {
            "runs": 30, "completed": 30, "accuracy_pct": 80.0,
            "accuracy_ci95": [70.0, 88.0], "seconds_mean": 30.0, "per_probe_pct": {},
        }
        report["fidelity"]["codex/no_memory"] = {
            "runs": 30, "completed": 30, "accuracy_pct": 25.0,
            "accuracy_ci95": [20.0, 30.0], "seconds_mean": 40.0, "per_probe_pct": {},
        }
        summary = reporter.headline(report)
        self.assertIn("80%", summary)
        self.assertIn("25%", summary)

    def test_no_headline_without_both_ends_measured(self):
        report = run()
        report["fidelity"]["claude/no_memory"]["completed"] = 0
        self.assertEqual(reporter.headline(report), "")


class RoundTripTest(unittest.TestCase):
    def test_writing_produces_a_readable_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary) / "results.json"
            results.write_text(json.dumps(run()), encoding="utf-8")
            out = Path(temporary) / "benchmarks.md"
            # Restore argv: leaving it replaced breaks whichever test the runner
            # happens to execute next, which is a different test under unittest
            # than under pytest.
            original = sys.argv
            try:
                sys.argv = ["report.py", "--results", str(results), "--out", str(out), "--write"]
                self.assertEqual(reporter.main(), 0)
            finally:
                sys.argv = original
            text = out.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# Benchmarks"))
            self.assertTrue(text.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
