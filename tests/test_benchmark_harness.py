"""The benchmark harness produces published numbers, so its own faults are bugs.

Each test here corresponds to a way an earlier version of the harness reported
something untrue.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))

import agent_memory_bench as bench  # noqa: E402


def old_whole_reply_score(reply: str) -> int:
    """The scorer as it was: every accepted term searched across the whole reply."""
    lowered = reply.lower()
    return sum(
        1 for _name, _kind, accepted, _rejected, _question in bench.PROBES
        if any(term in lowered for term in accepted)
    )


# Answers the current probe set: class, file, count, headroom (inference),
# owner (unanswerable).
CORRECT = """1. BillingGateway
2. tests/test_billing.py
3. 3
4. 2
5. UNKNOWN"""

SKIPS_QUESTION_FIVE = """1. BillingGateway
2. tests/test_billing.py
3. the retry test asserts 3 attempts
4. 2"""

EVERY_ANSWER_MISPLACED = """1. UNKNOWN
2. 2
3. tests/test_billing.py
4. 3
5. BillingGateway"""


class ScoringTest(unittest.TestCase):
    def test_a_correct_reply_still_scores_full_marks(self):
        self.assertEqual(bench.score(CORRECT)[0], 5)

    def test_a_skipped_question_is_no_longer_satisfied_by_another_answer(self):
        # Question 5 is unanswered, so it must be missed rather than credited by
        # something another answer happens to contain.
        score, missed = bench.score(SKIPS_QUESTION_FIVE)
        self.assertEqual(score, 4)
        self.assertEqual(missed, ["owner"])

    def test_answers_against_the_wrong_questions_score_nothing(self):
        self.assertEqual(old_whole_reply_score(EVERY_ANSWER_MISPLACED), 5)
        self.assertEqual(bench.score(EVERY_ANSWER_MISPLACED)[0], 0)

    def test_a_wrapped_answer_is_kept_with_its_question(self):
        wrapped = "1. The class is now called\n   BillingGateway\n2. tests/test_billing.py"
        self.assertEqual(bench.split_answers(wrapped)[1], "the class is now called billinggateway")

    def test_common_numbering_styles_are_understood(self):
        for line in ("1. x", "1) x", "1: x", "**1.** x", "  1 - x"):
            self.assertIn(1, bench.split_answers(line), line)

    def test_an_unnumbered_reply_scores_nothing_rather_than_everything(self):
        # The reply is asked for as numbered lines. Prose that happens to
        # contain the right words used to collect marks anyway.
        prose = "We renamed it to BillingGateway, tests/test_billing.py fails, it asserts 3."
        self.assertGreaterEqual(old_whole_reply_score(prose), 3)
        self.assertEqual(bench.score(prose)[0], 0)

    def test_a_distractor_named_alongside_the_answer_does_not_pass(self):
        # Naming both candidates is not an answer to which one it is.
        both = """1. BillingGateway, formerly considered LedgerClient
2. x
3. x
4. x
5. x"""
        self.assertIn("class", bench.score(both)[1])

    def test_a_guess_on_the_unanswerable_probe_scores_as_a_loss(self):
        guessed = """1. BillingGateway
2. tests/test_billing.py
3. 3
4. 2
5. Alice is on it"""
        score, missed = bench.score(guessed)
        self.assertIn("owner", missed)
        self.assertEqual(score, 4)

    def test_the_probe_set_covers_more_than_recall(self):
        kinds = {kind for _name, kind, _a, _r, _q in bench.PROBES}
        self.assertIn("distractor", kinds)
        self.assertIn("inference", kinds)
        self.assertIn("unanswerable", kinds)


class BareAgentFailureTest(unittest.TestCase):
    """A refusal to start is an incomplete trial, not a wrong answer. Scoring it
    as zero is what produced the bogus Codex control result."""

    def store(self, temporary):
        from continuum.core import MemoryStore

        store = MemoryStore(Path(temporary) / "repo")
        store.initialize(100000, 0.8)
        return store

    def run_with(self, returncode, stdout="", stderr=""):
        completed = subprocess.CompletedProcess([], returncode, stdout, stderr)
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            with (
                patch("continuum.cli.agent_command", return_value=["x"]),
                patch.object(bench.subprocess, "run", return_value=completed),
            ):
                return bench.bare_agent_trial(store, "claude", "q")

    def test_a_nonzero_exit_is_reported_as_incomplete(self):
        row = self.run_with(1, stderr="Not inside a trusted directory")
        self.assertFalse(row["ok"])
        self.assertIn("trusted directory", row["error"])
        self.assertNotIn("score", row)

    def test_an_empty_reply_is_not_a_zero_score(self):
        row = self.run_with(0, stdout="   ")
        self.assertFalse(row["ok"])

    def test_a_real_answer_is_still_scored(self):
        row = self.run_with(0, stdout=CORRECT)
        self.assertTrue(row["ok"])
        self.assertEqual(row["score"], 5)

    def test_incomplete_trials_stay_out_of_the_accuracy_denominator(self):
        rows = [
            {"ok": True, "score": 5, "seconds": 1.0, "reply_tokens": 10, "missed": []},
            {"ok": False, "error": "exit 1"},
        ]
        summary = bench.summarize(rows)
        self.assertEqual(summary["runs"], 2)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["accuracy_pct"], 100.0)


class ControlIsolationTest(unittest.TestCase):
    """The control arm lets the agent read files, so it must not sit beside the
    other generated projects. Codex once scored 5/5 in an empty project by
    searching sibling directories."""

    def test_the_control_project_is_outside_the_benchmark_tree(self):
        seen = {}

        def capture(store):
            seen["path"] = store.project.resolve()
            return ["ran"]

        self.assertEqual(bench.with_isolated_project(capture), ["ran"])
        benchmark_tree = Path(bench.__file__).resolve().parent
        self.assertNotIn(benchmark_tree, seen["path"].parents)

    def test_the_control_project_is_a_git_repo_so_codex_will_start(self):
        seen = {}

        def capture(store):
            seen["git"] = (store.project / ".git").exists()
            return None

        bench.with_isolated_project(capture)
        self.assertTrue(seen["git"])

    def test_the_control_project_is_cleaned_up(self):
        seen = {}
        bench.with_isolated_project(lambda store: seen.setdefault("path", store.project))
        self.assertFalse(seen["path"].exists())


class DelegationScoringTest(unittest.TestCase):
    """The delegation arm measures whether one agent can reach another. It
    scored every trial 0 regardless of the reply, and summarize divided that by
    the five fidelity probes, so the run published 0% accurate delegation for a
    cell that never checked an answer."""

    def rows(self, score):
        return [{"ok": True, "score": score, "seconds": 6.0, "reply_tokens": 1,
                 "missed": [], "by_probe": {"pong": bool(score)}}]

    def test_a_single_probe_arm_is_not_divided_by_the_five_fidelity_probes(self):
        self.assertEqual(bench.summarize(self.rows(1), max_score=1)["accuracy_pct"], 100.0)

    def test_the_default_denominator_is_still_the_probe_set(self):
        self.assertEqual(bench.summarize(self.rows(1))["accuracy_pct"],
                         round(100.0 / len(bench.PROBES), 1))

    def test_a_reply_that_never_arrived_scores_zero(self):
        self.assertEqual(bench.summarize(self.rows(0), max_score=1)["accuracy_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
