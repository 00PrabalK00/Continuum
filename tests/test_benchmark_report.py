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
        allowed = {"95", "100", "30", "20"}  # prose: interval level, axis, trials, the outlier factor
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
        for absent in ("Launching an agent", "## Categories", "## Which source"):
            self.assertNotIn(absent, text)

    def test_sections_appear_once_their_data_does(self):
        report = run(
            conflict={"claude": {"trials": 30, "answered_from_injected": 30, "answered_from_disk": 0}},
            categories=[{"agent": "claude", "category": "temporal", "passed": 29, "trials": 30}],
            delegation={"claude": {"completed": 30, "runs": 30, "seconds_mean": 7.3}},
        )
        text = reporter.render(report)
        self.assertIn("Launching an agent and getting its reply back", text)
        self.assertIn("## Categories", text)
        self.assertIn("29/30", text)
        self.assertIn("30/30", text)


class ContextSizeTest(unittest.TestCase):
    """estimate_tokens divides characters by four. Publishing the result as a
    token count states a precision the method does not have, and this README
    already says the estimate is not close enough to quote."""

    def test_the_token_column_is_marked_as_an_estimate(self):
        text = reporter.context_table(run(context_tokens={
            "raw_history": 1000, "compact": 100,
            "characters": {"raw_history": 4000, "compact": 400},
        }))
        self.assertIn("estimated tokens", text)
        self.assertIn("~1,000", text)

    def test_exact_characters_are_published_alongside(self):
        text = reporter.context_table(run(context_tokens={
            "raw_history": 1000, "compact": 100,
            "characters": {"raw_history": 4000, "compact": 400},
        }))
        self.assertIn("4,000", text)
        self.assertIn("400", text)

    def test_a_run_without_character_counts_still_renders(self):
        text = reporter.context_table(run(context_tokens={"raw_history": 1000, "compact": 100}))
        self.assertIn("~100", text)


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
            # --readme must point somewhere disposable. Without it, main()
            # defaults to the repository README and this test rewrites the
            # published figures with its own fixture.
            readme = Path(temporary) / "README.md"
            readme.write_text(
                "\n".join([reporter.README_START, "x", reporter.README_END]) + "\n",
                encoding="utf-8",
            )
            original = sys.argv
            try:
                sys.argv = ["report.py", "--results", str(results), "--out", str(out),
                            "--readme", str(readme), "--write"]
                self.assertEqual(reporter.main(), 0)
            finally:
                sys.argv = original
            text = out.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# Benchmarks"))
            self.assertTrue(text.endswith("\n"))


class SchemaGuardTest(unittest.TestCase):
    """The documented command is `report.py --write`, whose default results path
    is the pre-schema file holding the accuracy that docs/benchmarks.md
    withdrew. Rendering it would put those numbers back under a bootstrap
    heading."""

    def test_a_results_file_from_before_the_scorer_fix_is_refused(self):
        old = {"fidelity": {"claude/injected": {"accuracy_pct": 100.0}}}
        with self.assertRaises(reporter.IncompatibleResults):
            reporter.check(old)

    def test_the_refusal_says_what_to_do_instead(self):
        with self.assertRaises(reporter.IncompatibleResults) as caught:
            reporter.check({"schema": 1})
        self.assertIn("Re-run", str(caught.exception))

    def test_a_results_file_without_intervals_is_refused(self):
        stale = run(fidelity={"claude/injected": {"accuracy_pct": 100.0}})
        with self.assertRaises(reporter.IncompatibleResults):
            reporter.check(stale)

    def test_a_current_results_file_passes(self):
        reporter.check(run())


class CategoryDenominatorTest(unittest.TestCase):
    """An agent that never started did not answer badly."""

    def table(self, **row):
        base = {"category": "temporal", "agent": "claude", "passed": 1, "trials": 30}
        base.update(row)
        return reporter.category_table(run(categories=[base]))

    def test_trials_that_did_not_run_stay_out_of_the_denominator(self):
        text = self.table(passed=1, completed=1)
        self.assertIn("1/1", text)
        self.assertNotIn("1/30", text)

    def test_the_trials_that_did_not_run_are_still_disclosed(self):
        self.assertIn("29 did not run", self.table(passed=1, completed=1))

    def test_a_cell_where_nothing_completed_is_not_reported_as_zero_accuracy(self):
        text = self.table(passed=0, completed=0)
        self.assertIn("not measured", text)
        self.assertNotIn("0/30", text)

    def test_an_older_row_without_a_completed_count_still_renders(self):
        self.assertIn("5/30", self.table(passed=5))


class TimingTest(unittest.TestCase):
    def test_a_completed_cell_with_no_recorded_duration_does_not_crash(self):
        # summarize() records seconds_mean as None when no trial produced one.
        report = run(fidelity={"claude/injected": {
            "runs": 30, "completed": 30, "accuracy_pct": 100.0,
            "accuracy_ci95": [100.0, 100.0], "seconds_mean": None,
        }})
        self.assertIn("-", reporter.timing_table(report))


class TimingTableTest(unittest.TestCase):
    def entry(self, **fields):
        base = {"runs": 30, "completed": 30, "accuracy_pct": 100.0,
                "accuracy_ci95": [100.0, 100.0], "seconds_mean": 1217.5,
                "seconds_median": 41.3, "per_probe_pct": {}}
        base.update(fields)
        return base

    def test_the_median_is_published_not_the_mean(self):
        text = reporter.timing_table(run(fidelity={"claude/injected": self.entry()}))
        self.assertIn("41.3s", text)
        self.assertNotIn("1217.5s", text)

    def test_a_suspect_cell_says_so_rather_than_printing_a_number(self):
        text = reporter.timing_table(run(fidelity={
            "claude/injected": self.entry(timing_suspect=True)}))
        self.assertIn("not measured", text)
        self.assertNotIn("41.3s", text)

    def test_a_cell_recorded_before_the_check_existed_says_unchecked(self):
        entry = self.entry()
        entry.pop("timing_suspect", None)
        text = reporter.timing_table(run(fidelity={"claude/injected": entry}))
        self.assertIn("unchecked", text)

    def test_a_checked_cell_is_not_labelled_unchecked(self):
        text = reporter.timing_table(run(fidelity={
            "claude/injected": self.entry(timing_suspect=False)}))
        self.assertNotIn("unchecked", text)

    def test_an_older_entry_without_a_median_still_renders(self):
        entry = self.entry()
        entry.pop("seconds_median")
        text = reporter.timing_table(run(fidelity={"claude/injected": entry}))
        self.assertIn("1217.5s", text)


class DelegationTableTest(unittest.TestCase):
    """Delivery is measured only by a harness that read the reply."""

    def test_a_legacy_entry_is_not_published_as_zero_delivered(self):
        text = reporter.delegation_table(run(delegation={"claude": {
            "completed": 30, "runs": 30, "accuracy_pct": 0.0,
            "seconds_mean": 6.0, "per_probe_pct": {},
        }}))
        self.assertIn("not measured", text)
        self.assertNotIn("0%", text)

    def test_a_scored_entry_is_published(self):
        text = reporter.delegation_table(run(delegation={"claude": {
            "completed": 30, "runs": 30, "accuracy_pct": 100.0,
            "seconds_mean": 6.0, "per_probe_pct": {"pong": 100.0},
        }}))
        self.assertIn("100%", text)

    def test_the_round_trip_is_still_reported_either_way(self):
        text = reporter.delegation_table(run(delegation={"claude": {
            "completed": 30, "runs": 30, "accuracy_pct": 0.0,
            "seconds_mean": 6.0, "per_probe_pct": {},
        }}))
        self.assertIn("6.0s", text)


class ReadmeGenerationTest(unittest.TestCase):
    """This module claims the published figures are generated. That is only true
    if it writes the README block as well as the full page."""

    def readme(self, *lines):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, encoding="utf-8"
        )
        handle.write("\n".join(lines) + "\n")
        handle.close()
        return Path(handle.name)

    def wrapped(self, body):
        return self.readme("before", reporter.README_START, body,
                           reporter.README_END, "after")

    def test_the_delimited_block_is_replaced(self):
        path = self.wrapped("withdrawn")
        self.assertTrue(reporter.update_readme(run(), path))
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("withdrawn", text)
        self.assertIn("97%", text)

    def test_the_surrounding_prose_is_untouched(self):
        path = self.wrapped("x")
        reporter.update_readme(run(), path)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("before\n"))
        self.assertTrue(text.endswith("after\n"))

    def test_a_readme_without_the_block_is_reported_rather_than_mangled(self):
        path = self.readme("no markers here")
        self.assertFalse(reporter.update_readme(run(), path))
        self.assertEqual(path.read_text(encoding="utf-8"), "no markers here\n")

    def test_the_block_is_stable_so_reruns_do_not_nest_it(self):
        path = self.wrapped("x")
        reporter.update_readme(run(), path)
        first = path.read_text(encoding="utf-8")
        reporter.update_readme(run(), path)
        self.assertEqual(first, path.read_text(encoding="utf-8"))
        self.assertEqual(first.count(reporter.README_START), 1)

    def test_the_repository_readme_has_a_block_to_generate_into(self):
        readme = Path(__file__).resolve().parents[1] / "README.md"
        text = readme.read_text(encoding="utf-8")
        self.assertIn(reporter.README_START, text)
        self.assertIn(reporter.README_END, text)

    def test_the_context_saving_is_computed_rather_than_quoted(self):
        section = reporter.readme_section(run(
            context_tokens={"raw_history": 1000, "compact": 100}
        ))
        self.assertIn("90% smaller", section)

    def test_the_readme_block_states_no_figure_the_results_lack(self):
        report = run()
        section = reporter.readme_section(report)
        allowed = {"95", "100", "1000", "100"}
        for entry in report["fidelity"].values():
            allowed.add(f"{entry['accuracy_pct']:.0f}")
            allowed.add(f"{entry['seconds_mean']:.1f}")
            allowed.update(f"{bound:.0f}" for bound in entry["accuracy_ci95"])
        allowed.add(str(report["trials"]))
        allowed.add("90")
        for number in re.findall(r"\d+\.?\d*", section.replace(",", "")):
            self.assertIn(number, allowed, f"{number} is not in the results")


if __name__ == "__main__":
    unittest.main()
