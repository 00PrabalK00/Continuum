"""Checkpoints you can list, compare and return to.

Continuum kept the history and showed you only the newest state, so a context
that drifted somewhere wrong gave you no way to find where and no way back.
These cover the three commands that make the log usable, and the property that
matters most: restoring appends rather than erases.
"""

import argparse
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from continuum import history
from continuum.cli import (
    checkpoint_blame,
    checkpoint_diff,
    checkpoint_log,
    checkpoint_restore,
)
from continuum.core import MemoryStore
from continuum.progress import record_progress


def git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True
    ).stdout.strip()


def fresh(temporary: str) -> MemoryStore:
    store = MemoryStore(Path(temporary) / "repo")
    store.initialize(100000, 0.8)
    return store


def two(store: MemoryStore) -> None:
    record_progress(store, "picked PostgreSQL for the audit log", "write the schema")
    record_progress(store, "wrote the audit log schema", "add indexes")


def run(handler, store, **fields) -> str:
    out = StringIO()
    namespace = argparse.Namespace(project=str(store.project), vault=None, **fields)
    with redirect_stdout(out):
        handler(namespace)
    return out.getvalue()


class LogTest(unittest.TestCase):
    def test_checkpoints_are_listed_newest_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            lines = [line for line in run(checkpoint_log, store, limit=20).splitlines() if line]
            self.assertIn("wrote the audit log schema", lines[0])
            self.assertIn("picked PostgreSQL", lines[1])

    def test_an_empty_project_says_how_to_record_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIn("continuum save", run(checkpoint_log, fresh(temporary), limit=20))

    def test_the_limit_is_respected(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            for index in range(5):
                record_progress(store, f"thing {index}", f"next {index}")
            lines = [line for line in run(checkpoint_log, store, limit=2).splitlines() if line]
            self.assertEqual(len(lines), 2)


class FindTest(unittest.TestCase):
    def test_the_printed_form_resolves(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            newest = history.checkpoints(store, 1)[0]
            self.assertEqual(history.find(store, history.label(newest))["id"], newest["id"])

    def test_a_bare_number_resolves_too(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            newest = history.checkpoints(store, 1)[0]
            self.assertEqual(history.find(store, str(newest["id"]))["id"], newest["id"])

    def test_head_is_the_newest(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            self.assertEqual(history.find(store, "HEAD")["id"],
                             history.checkpoints(store, 1)[0]["id"])

    def test_an_unknown_checkpoint_says_how_to_find_the_real_ones(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            with self.assertRaises(history.HistoryError) as caught:
                history.find(store, "C9999")
            self.assertIn("continuum log", str(caught.exception))

    def test_nonsense_is_rejected_rather_than_read_as_a_number(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            with self.assertRaises(history.HistoryError):
                history.find(store, "yesterday")


class RenderingTest(unittest.TestCase):
    """The log is one line per checkpoint. A caller-supplied task can be long or
    multiline, and printed raw it breaks that shape."""

    def test_a_multiline_task_stays_on_one_line(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "first line\nsecond line\nthird line", "next")
            body = run(checkpoint_log, store, limit=20)
            self.assertEqual(len([line for line in body.splitlines() if line]), 1)

    def test_a_very_long_task_is_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "x" * 5_000, "next")
            body = run(checkpoint_log, store, limit=20).strip()
            self.assertLess(len(body), 200)
            self.assertTrue(body.endswith("…"))

    def test_an_ordinary_task_is_untouched(self):
        self.assertEqual(history.one_line("wrote the audit log schema"),
                         "wrote the audit log schema")


class OldHistoryTest(unittest.TestCase):
    def test_a_checkpoint_beyond_the_recent_window_still_resolves(self):
        # A fixed scan of the newest N rejected checkpoints that `continuum log
        # --limit` would happily display.
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "the first thing", "the first step")
            oldest = history.checkpoints(store, 1)[0]
            for index in range(30):
                record_progress(store, f"thing {index}", f"step {index}")
            self.assertEqual(history.find(store, history.label(oldest))["id"], oldest["id"])

    def test_a_non_checkpoint_event_is_named_as_such(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            store.event("agent_exit", {"summary": "not a checkpoint", "returncode": 0})
            recorded = store.recent_events(1)[0]
            with self.assertRaises(history.HistoryError) as caught:
                history.find(store, str(recorded["id"]))
            self.assertIn("not a checkpoint", str(caught.exception))


class DiffTest(unittest.TestCase):
    def test_the_default_compares_the_newest_two(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            text = run(checkpoint_diff, store, older=None, newer=None)
            self.assertIn("- picked PostgreSQL for the audit log", text)
            self.assertIn("+ wrote the audit log schema", text)

    def test_one_argument_means_since_that_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            record_progress(store, "added the indexes", "measure the query")
            oldest = history.checkpoints(store, 3)[-1]
            text = run(checkpoint_diff, store, older=history.label(oldest), newer=None)
            self.assertIn("+ added the indexes", text)

    def test_unchanged_fields_are_not_printed(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "same task", "first step")
            record_progress(store, "same task", "second step")
            text = run(checkpoint_diff, store, older=None, newer=None)
            self.assertNotIn("same task", text)
            self.assertIn("second step", text)

    def test_two_identical_checkpoints_say_so_rather_than_printing_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            newest = history.checkpoints(store, 1)[0]
            self.assertIn("Nothing changed", history.render_diff(store, newest, newest))

    def test_a_moved_commit_is_shown(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            project.mkdir()
            git(project, "init")
            git(project, "config", "user.email", "t@example.com")
            git(project, "config", "user.name", "Test")
            store = MemoryStore(project)
            store.initialize(100000, 0.8)
            (project / "a.py").write_text("a", encoding="utf-8")
            git(project, "add", "a.py")
            git(project, "commit", "-m", "first")
            record_progress(store, "first thing", "do the second")
            (project / "b.py").write_text("b", encoding="utf-8")
            git(project, "add", "b.py")
            git(project, "commit", "-m", "second")
            record_progress(store, "second thing", "do the third")
            self.assertIn("Commit", run(checkpoint_diff, store, older=None, newer=None))

    def test_a_single_checkpoint_cannot_be_compared(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "only thing", "only step")
            with self.assertRaises(SystemExit):
                run(checkpoint_diff, store, older=None, newer=None)


class RestoreTest(unittest.TestCase):
    """Restoring appends. Rewriting the log to make an old state look current is
    exactly the lie this whole layer exists to prevent."""

    def test_the_restored_state_becomes_current(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            oldest = history.checkpoints(store, 2)[-1]
            run(checkpoint_restore, store, checkpoint=history.label(oldest))
            self.assertEqual(store.latest_task(),
                             ("picked PostgreSQL for the audit log", "write the schema"))

    def test_the_history_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            before = [item["id"] for item in history.checkpoints(store, 10)]
            oldest = history.checkpoints(store, 2)[-1]
            run(checkpoint_restore, store, checkpoint=history.label(oldest))
            after = [item["id"] for item in history.checkpoints(store, 10)]
            self.assertEqual(after[1:], before)
            self.assertEqual(len(after), len(before) + 1)

    def test_the_restore_is_recorded_as_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            oldest = history.checkpoints(store, 2)[-1]
            run(checkpoint_restore, store, checkpoint=history.label(oldest))
            self.assertEqual(history.checkpoints(store, 1)[0]["payload"]["source"], "restore")

    def test_it_says_the_history_is_intact(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            oldest = history.checkpoints(store, 2)[-1]
            text = run(checkpoint_restore, store, checkpoint=history.label(oldest))
            self.assertIn("history is unchanged", text)

    def test_a_checkpoint_with_no_next_step_does_not_gain_one(self):
        # record_progress fills in a missing next step from the current state or
        # the handoff model. Restoring through it produced a state that never
        # existed: the old task carrying the current next step.
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "just the task", "")
            bare = history.checkpoints(store, 1)[0]
            record_progress(store, "later work", "a step that must not be copied")
            run(checkpoint_restore, store, checkpoint=history.label(bare))
            self.assertEqual(store.latest_task()[0], "just the task")
            self.assertNotEqual(store.latest_task()[1], "a step that must not be copied")

    def test_the_source_checkpoint_is_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            oldest = history.checkpoints(store, 2)[-1]
            run(checkpoint_restore, store, checkpoint=history.label(oldest))
            self.assertEqual(history.checkpoints(store, 1)[0]["payload"]["restored_from"],
                             oldest["id"])

    def test_an_unknown_checkpoint_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            two(store)
            with self.assertRaises(history.HistoryError):
                run(checkpoint_restore, store, checkpoint="C9999")


class BlameTest(unittest.TestCase):
    """An agent reading current.md is told a thing and cannot ask where it came
    from. Blame answers with a checkpoint, a date and a commit, and infers
    nothing beyond where the words appear."""

    def history(self, store):
        record_progress(store, "considered LedgerClient for the payment client", "decide the name")
        record_progress(store, "renamed the payment client to BillingGateway", "migrate callers")
        record_progress(store, "migrated the callers to BillingGateway", "fix the retry test")

    def test_the_first_checkpoint_to_mention_it_is_named(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            self.history(store)
            oldest = history.checkpoints(store, 3)[-1]
            text = run(checkpoint_blame, store, text=["LedgerClient"])
            self.assertIn(history.label(oldest), text)

    def test_a_term_still_current_says_where_it_was_last_seen(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            self.history(store)
            newest = history.checkpoints(store, 1)[0]
            text = run(checkpoint_blame, store, text=["BillingGateway"])
            self.assertIn("Still present in", text)
            self.assertIn(history.label(newest), text)

    def test_a_term_recorded_once_says_so(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            self.history(store)
            self.assertIn("not repeated since",
                          run(checkpoint_blame, store, text=["LedgerClient"]))

    def test_an_absent_term_points_at_the_wider_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            self.history(store)
            text = run(checkpoint_blame, store, text=["PostgreSQL"])
            self.assertIn("No checkpoint mentions", text)
            self.assertIn("continuum search", text)

    def test_the_match_ignores_case(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            self.history(store)
            self.assertEqual(len(history.blame(store, "billinggateway")), 2)

    def test_the_next_step_is_searched_as_well_as_the_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "did a thing", "fix the retry test")
            self.assertEqual(len(history.blame(store, "retry test")), 1)

    def test_results_are_oldest_first_so_the_first_mention_is_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            self.history(store)
            found = history.blame(store, "BillingGateway")
            self.assertLess(found[0]["id"], found[-1]["id"])

    def test_the_recording_commit_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            project.mkdir()
            git(project, "init")
            git(project, "config", "user.email", "t@example.com")
            git(project, "config", "user.name", "Test")
            store = MemoryStore(project)
            store.initialize(100000, 0.8)
            (project / "a.py").write_text("a", encoding="utf-8")
            git(project, "add", "a.py")
            git(project, "commit", "-m", "first")
            sha = git(project, "rev-parse", "HEAD")
            record_progress(store, "renamed it to BillingGateway", "migrate callers")
            self.assertIn(sha[:7], run(checkpoint_blame, store, text=["BillingGateway"]))

    def test_a_term_older_than_any_recent_window_is_still_attributed(self):
        # "first recorded in C3" is a claim about everything before it. A
        # recent-window search is wrong in two directions at once: a term only
        # older than the window reads as never recorded, and one repeated inside
        # it is attributed to the later mention as though that were the first.
        # 600 checkpoints, comfortably past the 500 this used to scan.
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "renamed it to BillingGateway", "chose PostgreSQL")
            oldest = history.checkpoints(store, 1)[0]
            for index in range(600):
                record_progress(store, f"routine work {index}", f"next {index}")
            self.assertEqual(history.blame(store, "BillingGateway")[0]["id"], oldest["id"])
            self.assertNotIn("No checkpoint mentions",
                             run(checkpoint_blame, store, text=["PostgreSQL"]))

    def test_a_payload_key_is_not_mistaken_for_a_recorded_word(self):
        # The database narrows on the raw payload, so "next_step" and "commit"
        # appear in every row. Only the recorded task and next step count.
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            self.history(store)
            self.assertEqual(history.blame(store, "next_step"), [])

    def test_an_empty_query_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            with self.assertRaises(SystemExit):
                run(checkpoint_blame, store, text=["   "])


if __name__ == "__main__":
    unittest.main()
