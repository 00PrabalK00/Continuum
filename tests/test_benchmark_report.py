"""The report generator publishes numbers, so a fault here republishes a fault.

Each test corresponds to a way this generator would have printed something
untrue: reading a results file whose accuracy was withdrawn, counting an agent
that failed to start as an agent that answered wrongly, or leaving the README
figures hand-copied while claiming they were generated.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))

import report  # noqa: E402


def results(**overrides):
    base = {
        "schema": report.SCHEMA,
        "trials": 30,
        "probe_kinds": {
            "class": "distractor", "file": "recall", "count": "recall",
            "headroom": "inference", "owner": "unanswerable",
        },
        "context_tokens": {"raw_history": 1710, "compact": 109},
        "fidelity": {
            "claude/injected": {
                "accuracy_pct": 100.0,
                "accuracy_ci95": [100.0, 100.0],
                "completed": 30,
                "runs": 30,
                "seconds_mean": 5.4,
            }
        },
        "categories": [],
    }
    base.update(overrides)
    return base


class SchemaGuardTest(unittest.TestCase):
    """The documented command is `report.py --write`, whose default results path
    is the pre-schema file holding the accuracy that docs/benchmarks.md
    withdrew. Rendering it would put those numbers back under a bootstrap
    heading."""

    def test_a_results_file_from_before_the_scorer_fix_is_refused(self):
        old = {"fidelity": {"claude/injected": {"accuracy_pct": 100.0}}}
        with self.assertRaises(report.IncompatibleResults):
            report.check(old)

    def test_the_refusal_says_what_to_do_instead(self):
        with self.assertRaises(report.IncompatibleResults) as caught:
            report.check({"schema": 1})
        self.assertIn("Re-run", str(caught.exception))

    def test_a_results_file_without_intervals_is_refused(self):
        stale = results(fidelity={"claude/injected": {"accuracy_pct": 100.0}})
        with self.assertRaises(report.IncompatibleResults):
            report.check(stale)

    def test_a_current_results_file_passes(self):
        report.check(results())


class CategoryDenominatorTest(unittest.TestCase):
    """An agent that never started did not answer badly."""

    def test_trials_that_did_not_run_stay_out_of_the_denominator(self):
        text = report.category_table(results(categories=[
            {"category": "temporal", "agent": "claude", "passed": 1, "trials": 30, "completed": 1},
        ]))
        self.assertIn("1/1", text)
        self.assertNotIn("1/30", text)

    def test_the_trials_that_did_not_run_are_still_disclosed(self):
        text = report.category_table(results(categories=[
            {"category": "temporal", "agent": "claude", "passed": 1, "trials": 30, "completed": 1},
        ]))
        self.assertIn("29 did not run", text)

    def test_a_cell_where_nothing_completed_is_not_reported_as_zero_accuracy(self):
        text = report.category_table(results(categories=[
            {"category": "temporal", "agent": "claude", "passed": 0, "trials": 30, "completed": 0},
        ]))
        self.assertIn("not measured", text)
        self.assertNotIn("0/30", text)

    def test_an_older_row_without_a_completed_count_still_renders(self):
        text = report.category_table(results(categories=[
            {"category": "temporal", "agent": "claude", "passed": 5, "trials": 30},
        ]))
        self.assertIn("5/30", text)


class ReadmeGenerationTest(unittest.TestCase):
    """This module claims the published figures are generated. That is only true
    if it writes the README block as well as the full page."""

    def readme(self, body):
        handle = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        handle.write(body)
        handle.close()
        return Path(handle.name)

    def test_the_delimited_block_is_replaced(self):
        path = self.readme(
            f"before\n{report.README_START}\nwithdrawn\n{report.README_END}\nafter\n"
        )
        self.assertTrue(report.update_readme(results(), path))
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("withdrawn", text)
        self.assertIn("100%", text)

    def test_the_surrounding_prose_is_untouched(self):
        path = self.readme(
            f"before\n{report.README_START}\nx\n{report.README_END}\nafter\n"
        )
        report.update_readme(results(), path)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("before\n"))
        self.assertTrue(text.endswith("after\n"))

    def test_a_readme_without_the_block_is_reported_rather_than_mangled(self):
        path = self.readme("no markers here\n")
        self.assertFalse(report.update_readme(results(), path))
        self.assertEqual(path.read_text(encoding="utf-8"), "no markers here\n")

    def test_the_block_is_stable_so_reruns_do_not_nest_it(self):
        path = self.readme(f"a\n{report.README_START}\nx\n{report.README_END}\nb\n")
        report.update_readme(results(), path)
        first = path.read_text(encoding="utf-8")
        report.update_readme(results(), path)
        self.assertEqual(first, path.read_text(encoding="utf-8"))
        self.assertEqual(first.count(report.README_START), 1)

    def test_the_repository_readme_has_a_block_to_generate_into(self):
        readme = Path(__file__).resolve().parents[1] / "README.md"
        text = readme.read_text(encoding="utf-8")
        self.assertIn(report.README_START, text)
        self.assertIn(report.README_END, text)

    def test_a_completed_cell_with_no_recorded_duration_does_not_crash(self):
        # summarize() records seconds_mean as None when no trial produced one.
        text = report.render(results(fidelity={
            "claude/injected": {
                "accuracy_pct": 100.0, "accuracy_ci95": [100.0, 100.0],
                "completed": 30, "runs": 30, "seconds_mean": None,
            }
        }))
        self.assertIn("-", text)

    def test_the_context_saving_is_computed_rather_than_quoted(self):
        section = report.readme_section(results(
            context_tokens={"raw_history": 1000, "compact": 100}
        ))
        self.assertIn("90% smaller", section)


if __name__ == "__main__":
    unittest.main()
