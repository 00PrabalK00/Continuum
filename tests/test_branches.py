"""Two agents on one project used to share one line of context, and the newer
save silently erased the older. Branches give them separate lines; merge is
where a disagreement has to be faced rather than settled by whoever wrote last.
Last write wins is the behaviour a version control system exists to refuse, so
the conflict path is the one worth being strict about.
"""

import argparse
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from continuum import history
from continuum.cli import context_branch, context_merge, quick_status
from continuum.core import MemoryStore
from continuum.progress import record_progress


def fresh(temporary: str) -> MemoryStore:
    store = MemoryStore(Path(temporary) / "repo")
    store.initialize(100000, 0.8)
    return store


def run(handler, store, **fields) -> str:
    out = StringIO()
    with redirect_stdout(out):
        handler(argparse.Namespace(project=str(store.project), vault=None, **fields))
    return out.getvalue()


def diverged(store):
    """One recorded state, then two branches that disagree about it."""
    record_progress(store, "renamed the payment client", "migrate callers")
    history.switch(store, "codex-lane")
    record_progress(store, "renamed it to LedgerClient", "migrate callers")
    history.switch(store, "main")
    record_progress(store, "renamed it to BillingGateway", "migrate callers")


class BranchTest(unittest.TestCase):
    def test_a_project_starts_on_main(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(fresh(temporary).current_branch(), "main")

    def test_checkpoints_on_one_branch_stay_off_the_other(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "work on main", "next on main")
            history.switch(store, "side")
            record_progress(store, "work on side", "next on side")
            self.assertEqual(store.latest_task()[0], "work on side")
            history.switch(store, "main")
            self.assertEqual(store.latest_task()[0], "work on main")

    def test_a_new_branch_starts_from_where_you_are(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "renamed the payment client", "migrate callers")
            history.switch(store, "side")
            self.assertEqual(store.latest_task(),
                             ("renamed the payment client", "migrate callers"))

    def test_the_starting_point_records_where_it_came_from(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "work on main", "next on main")
            history.switch(store, "side")
            payload = store.recent_handoffs(1)[0]["payload"]
            self.assertEqual(payload["source"], "branch")
            self.assertEqual(payload["branched_from"], "main")

    def test_the_files_an_agent_reads_follow_the_branch(self):
        # current.md is what a launched agent is handed. Changing only the
        # recorded branch would hand over the previous branch's work while the
        # status card claimed otherwise.
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "work on main", "next on main")
            history.switch(store, "side")
            record_progress(store, "work on side", "next on side")
            history.switch(store, "main")
            current = (store.state_dir / "current.md").read_text(encoding="utf-8")
            self.assertIn("work on main", current)
            self.assertNotIn("work on side", current)

    def test_every_branch_with_a_checkpoint_is_listed(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "work on main", "next")
            history.switch(store, "side")
            self.assertEqual(store.branches(), ["main", "side"])

    def test_checkpoints_from_before_branches_existed_are_on_main(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            store.event("handoff", {"task": "written before branches", "next_step": "carry on"})
            self.assertEqual(store.latest_task()[0], "written before branches")
            self.assertEqual(store.branches(), ["main"])

    def test_the_status_card_names_a_branch_that_is_not_main(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "work", "next")
            history.switch(store, "side")
            self.assertIn("[side]", run(quick_status, store))

    def test_the_status_card_stays_quiet_on_main(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "work", "next")
            self.assertNotIn("[main]", run(quick_status, store))

    def test_switching_to_the_current_branch_changes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "work", "next")
            before = len(store.recent_handoffs(50))
            self.assertIn("Already on branch", run(context_branch, store, name="main"))
            self.assertEqual(len(store.recent_handoffs(50)), before)


class MergeTest(unittest.TestCase):
    def test_a_field_only_one_side_moved_is_taken(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "renamed the payment client", "migrate callers")
            history.switch(store, "side")
            record_progress(store, "renamed the payment client", "write the retry test")
            history.switch(store, "main")
            merged = history.merge(store, "side")
            self.assertEqual(merged["next_step"], "write the retry test")
            self.assertEqual(merged["resolved"], [])

    def test_two_sides_changing_the_same_field_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            diverged(store)
            with self.assertRaises(history.MergeConflict) as caught:
                history.merge(store, "codex-lane")
            self.assertIn("task", caught.exception.fields)

    def test_the_refusal_shows_both_claims(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            diverged(store)
            with self.assertRaises(history.MergeConflict) as caught:
                history.merge(store, "codex-lane")
            report = str(caught.exception)
            self.assertIn("BillingGateway", report)
            self.assertIn("LedgerClient", report)

    def test_a_refused_merge_records_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            diverged(store)
            before = len(store.recent_handoffs(50))
            with self.assertRaises(history.MergeConflict):
                history.merge(store, "codex-lane")
            self.assertEqual(len(store.recent_handoffs(50)), before)
            self.assertEqual(store.latest_task()[0], "renamed it to BillingGateway")

    def test_the_conflict_exits_nonzero_rather_than_raising_at_the_user(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            diverged(store)
            out = StringIO()
            with redirect_stdout(out):
                code = context_merge(argparse.Namespace(
                    project=str(store.project), vault=None,
                    branch="codex-lane", theirs=False))
            self.assertEqual(code, 1)
            self.assertIn("both changed the same thing", out.getvalue())

    def test_theirs_resolves_it_and_says_what_it_took(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            diverged(store)
            merged = history.merge(store, "codex-lane", force=True)
            self.assertEqual(merged["task"], "renamed it to LedgerClient")
            self.assertEqual(merged["resolved"], ["task"])

    def test_the_merge_is_recorded_with_its_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "shared task", "migrate callers")
            history.switch(store, "side")
            record_progress(store, "shared task", "write the retry test")
            history.switch(store, "main")
            history.merge(store, "side")
            payload = store.recent_handoffs(1)[0]["payload"]
            self.assertEqual(payload["source"], "merge")
            self.assertEqual(payload["merged_from"], "side")

    def test_merging_the_current_branch_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "work", "next")
            with self.assertRaises(history.HistoryError):
                history.merge(store, "main")

    def test_an_unknown_branch_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "work", "next")
            with self.assertRaises(history.HistoryError) as caught:
                history.merge(store, "nowhere")
            self.assertIn("continuum branch", str(caught.exception))

    def test_the_merged_state_reaches_the_files_an_agent_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "shared task", "migrate callers")
            history.switch(store, "side")
            record_progress(store, "shared task", "write the retry test")
            history.switch(store, "main")
            history.merge(store, "side")
            current = (store.state_dir / "current.md").read_text(encoding="utf-8")
            self.assertIn("write the retry test", current)


if __name__ == "__main__":
    unittest.main()
