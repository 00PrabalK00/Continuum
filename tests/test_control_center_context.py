"""The endpoints the rebuilt page is built on.

The old Control Center exposed orchestration: teams, providers, worktrees, ROI.
None of it answers the question someone opens the page to ask, which is where
this project stands and whether that is still true. These are the reads that do.
"""

import tempfile
import unittest
from pathlib import Path

from continuum import history, notes
from continuum.control_center import ControlCenter
from continuum.core import MemoryStore
from continuum.progress import record_progress


def prepared(temporary: str):
    project = Path(temporary) / "repo"
    store = MemoryStore(project)
    store.initialize(100000, 0.8)
    record_progress(store, "renamed the payment client to BillingGateway", "fix the retry test")
    notes.record(store, "decision", "chose PostgreSQL over MySQL")
    notes.record(store, "hypothesis", "the retry test fails on the timeout")
    notes.record(store, "fact", "the retry test asserts 3 attempts")
    record_progress(store, "migrated the callers", "run the suite")
    return store, ControlCenter(project)


class NowTest(unittest.TestCase):
    """One call, because the page's primary answer should not be stitched
    together by the browser from four requests."""

    def test_it_answers_where_the_project_stands(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, app = prepared(temporary)
            now = app.now()
            self.assertEqual(now["task"], "migrated the callers")
            self.assertEqual(now["next_step"], "run the suite")

    def test_it_carries_the_typed_claims(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, app = prepared(temporary)
            now = app.now()
            self.assertIn("chose PostgreSQL over MySQL", now["decisions"])
            self.assertIn("the retry test fails on the timeout", now["open_questions"])
            self.assertIn("the retry test asserts 3 attempts", now["facts"])

    def test_a_resolved_hypothesis_leaves_the_open_questions(self):
        with tempfile.TemporaryDirectory() as temporary:
            store, app = prepared(temporary)
            raised = [item for item in notes.recent(store) if item["type"] == "hypothesis"][0]
            notes.resolve(store, raised["id"], notes.CONFIRMED)
            self.assertEqual(app.now()["open_questions"], [])

    def test_it_reports_freshness_rather_than_implying_none(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, app = prepared(temporary)
            now = app.now()
            self.assertIn("age_days", now)
            self.assertIn("drift", now)

    def test_it_names_the_branch(self):
        with tempfile.TemporaryDirectory() as temporary:
            store, app = prepared(temporary)
            history.switch(store, "side")
            self.assertEqual(app.now()["branch"], "side")

    def test_an_empty_project_does_not_raise(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "empty"
            store = MemoryStore(project)
            store.initialize(100000, 0.8)
            now = ControlCenter(project).now()
            self.assertEqual(now["task"], "")
            self.assertEqual(now["decisions"], [])


class CheckpointTest(unittest.TestCase):
    def test_checkpoints_are_listed_newest_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, app = prepared(temporary)
            found = app.checkpoints()
            self.assertEqual(found[0]["task"], "migrated the callers")
            self.assertTrue(found[0]["ref"].startswith("C"))

    def test_a_long_task_is_bounded_for_the_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            store, app = prepared(temporary)
            record_progress(store, "x" * 400, "next")
            self.assertLess(len(app.checkpoints()[0]["task"]), 200)

    def test_two_checkpoints_can_be_compared(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, app = prepared(temporary)
            found = app.checkpoints()
            diff = app.checkpoint_diff(found[-1]["ref"], found[0]["ref"])
            self.assertIn("migrated the callers", diff["diff"])
            self.assertIn("renamed the payment client", diff["diff"])

    def test_an_unknown_checkpoint_is_an_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, app = prepared(temporary)
            with self.assertRaises(history.HistoryError):
                app.checkpoint_diff("C9999", "HEAD")


class BlameTest(unittest.TestCase):
    def test_it_finds_where_a_claim_entered(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, app = prepared(temporary)
            found = app.blame("BillingGateway")
            self.assertIn("first recorded", found["summary"])
            self.assertTrue(found["matches"])

    def test_an_empty_query_returns_nothing_rather_than_everything(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, app = prepared(temporary)
            self.assertEqual(app.blame("   ")["matches"], [])

    def test_an_absent_term_says_so(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, app = prepared(temporary)
            self.assertIn("No checkpoint mentions", app.blame("Redis")["summary"])


class NotesTest(unittest.TestCase):
    def test_every_kind_is_listed_with_its_type(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, app = prepared(temporary)
            kinds = {item["type"] for item in app.notes()}
            self.assertEqual(kinds, {"decision", "hypothesis", "fact"})

    def test_an_open_hypothesis_carries_its_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, app = prepared(temporary)
            hypothesis = next(item for item in app.notes() if item["type"] == "hypothesis")
            self.assertEqual(hypothesis["state"], "open")


class BranchTest(unittest.TestCase):
    def test_the_current_branch_is_marked(self):
        with tempfile.TemporaryDirectory() as temporary:
            store, app = prepared(temporary)
            history.switch(store, "side")
            current = [item for item in app.branches() if item["current"]]
            self.assertEqual([item["name"] for item in current], ["side"])

    def test_each_branch_reports_what_it_holds(self):
        with tempfile.TemporaryDirectory() as temporary:
            store, app = prepared(temporary)
            history.switch(store, "side")
            record_progress(store, "work on the side branch", "next")
            side = next(item for item in app.branches() if item["name"] == "side")
            self.assertEqual(side["task"], "work on the side branch")
            self.assertGreater(side["checkpoints"], 0)


if __name__ == "__main__":
    unittest.main()
