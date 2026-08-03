"""Notes that say what kind of claim they are.

Everything recorded used to arrive at the next agent in one voice, so "we chose
PostgreSQL" and "the retry test probably fails on the timeout" read identically.
A guess made on Tuesday came back on Friday sounding like something the project
had settled. These cover the three kinds, the open-until-resolved rule that
makes a hypothesis worth marking, and whether any of it reaches an agent.
"""

import argparse
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from continuum import history, notes
from continuum.cli import context_note
from continuum.core import MemoryStore
from continuum.mcp_server import call_tool, tool_definitions
from continuum.progress import record_progress


def fresh(temporary: str) -> MemoryStore:
    store = MemoryStore(Path(temporary) / "repo")
    store.initialize(100000, 0.8)
    return store


def run(store, kind=None, text=()) -> str:
    out = StringIO()
    with redirect_stdout(out):
        context_note(argparse.Namespace(
            project=str(store.project), vault=None, kind=kind, text=list(text)))
    return out.getvalue()


class RecordTest(unittest.TestCase):
    def test_each_kind_is_recorded_with_its_type(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            for kind in notes.KINDS:
                notes.record(store, kind, f"a {kind}")
            self.assertEqual({item["type"] for item in notes.recent(store)}, set(notes.KINDS))

    def test_an_unknown_kind_is_refused_and_lists_the_real_ones(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            with self.assertRaises(notes.ClaimError) as caught:
                notes.record(store, "guess", "something")
            self.assertIn("decision", str(caught.exception))

    def test_an_empty_claim_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(notes.ClaimError):
                notes.record(fresh(temporary), "fact", "   ")

    def test_a_hypothesis_starts_open_and_the_others_do_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            self.assertEqual(notes.record(store, "hypothesis", "maybe")["state"], notes.OPEN)
            self.assertEqual(notes.record(store, "decision", "chose")["state"], "")

    def test_the_recording_commit_and_branch_are_kept(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            recorded = notes.record(store, "fact", "the test asserts 3 attempts")
            self.assertIn("commit", recorded)
            self.assertEqual(recorded["branch"], "main")


class ResolveTest(unittest.TestCase):
    """A hypothesis nobody resolves looks more certain every time it is carried
    forward, which is the failure this exists to prevent."""

    def raised(self, store):
        notes.record(store, "hypothesis", "the retry test fails on the timeout")
        return notes.recent(store)[0]["id"]

    def test_confirming_marks_it_confirmed(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            notes.resolve(store, self.raised(store), notes.CONFIRMED)
            self.assertEqual(notes.recent(store)[0]["state"], notes.CONFIRMED)

    def test_dropping_marks_it_dropped(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            notes.resolve(store, self.raised(store), notes.DROPPED)
            self.assertEqual(notes.recent(store)[0]["state"], notes.DROPPED)

    def test_the_original_claim_is_not_rewritten(self):
        # A hypothesis quietly edited into a fact loses the fact that anyone
        # doubted it, so the resolution is its own event.
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            claim_id = self.raised(store)
            notes.resolve(store, claim_id, notes.CONFIRMED)
            kinds = [item["kind"] for item in store.recent_events(20)]
            self.assertIn("claim", kinds)
            self.assertIn("claim_resolved", kinds)

    def test_only_a_hypothesis_can_be_resolved(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            notes.record(store, "fact", "the test asserts 3 attempts")
            recorded = notes.recent(store)[0]["id"]
            with self.assertRaises(notes.ClaimError) as caught:
                notes.resolve(store, recorded, notes.CONFIRMED)
            self.assertIn("only a hypothesis", str(caught.exception))

    def test_an_unknown_claim_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(notes.ClaimError):
                notes.resolve(fresh(temporary), 9999, notes.CONFIRMED)

    def test_a_made_up_resolution_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            with self.assertRaises(notes.ClaimError):
                notes.resolve(store, self.raised(store), "probably")


class ContextTest(unittest.TestCase):
    """Typed claims that never reach an agent are only a filing system."""

    def prepared(self, temporary):
        store = fresh(temporary)
        record_progress(store, "renamed the payment client", "migrate callers")
        notes.record(store, "decision", "chose PostgreSQL over MySQL")
        notes.record(store, "hypothesis", "the retry test fails on the timeout")
        return store

    def test_a_decision_reaches_the_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.prepared(temporary)
            self.assertIn("chose PostgreSQL", store.resume_context("compact"))

    def test_an_open_hypothesis_is_labelled_as_unsettled(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.prepared(temporary)
            context = store.resume_context("compact")
            self.assertIn("not settled", context)
            self.assertIn("the retry test fails on the timeout", context)

    def test_a_resolved_hypothesis_stops_being_carried_as_a_question(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.prepared(temporary)
            raised = [item for item in notes.recent(store) if item["type"] == "hypothesis"][0]
            notes.resolve(store, raised["id"], notes.CONFIRMED)
            self.assertNotIn("not settled", store.resume_context("compact"))

    def test_a_project_with_no_claims_gains_no_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "work", "next")
            self.assertNotIn("Decisions:", store.resume_context("compact"))

    def test_claims_follow_the_branch(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.prepared(temporary)
            history.switch(store, "side")
            notes.record(store, "decision", "a decision made on the side branch")
            self.assertIn("side branch", store.resume_context("compact"))
            history.switch(store, "main")
            self.assertNotIn("side branch", store.resume_context("compact"))

    def test_the_block_is_bounded(self):
        # It is prepended to context that is deliberately small, so listing
        # everything ever decided would defeat the compaction it rides on.
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "work", "next")
            for index in range(20):
                notes.record(store, "decision", f"decision number {index}")
            self.assertNotIn("decision number 0", store.resume_context("compact"))


class CommandTest(unittest.TestCase):
    def test_recording_says_a_hypothesis_stays_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            self.assertIn("until you confirm or drop", run(store, "hypothesis", ["maybe this"]))

    def test_listing_an_empty_project_says_how_to_record_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIn("continuum note", run(fresh(temporary)))

    def test_confirm_needs_an_id_rather_than_prose(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            with self.assertRaises(SystemExit):
                run(store, "confirm", ["the retry thing"])

    def test_the_listing_shows_the_open_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            notes.record(store, "hypothesis", "maybe this")
            self.assertIn("[open]", run(store))


class McpTest(unittest.TestCase):
    def test_the_tool_explains_which_kind_to_use(self):
        tool = next(item for item in tool_definitions() if item["name"] == "record_claim")
        for kind in notes.KINDS:
            self.assertIn(kind, tool["description"])

    def test_an_agent_can_record_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            call_tool(store, "record_claim", {"type": "decision", "text": "chose PostgreSQL"})
            recorded = notes.recent(store)[0]
            self.assertEqual(recorded["text"], "chose PostgreSQL")
            self.assertEqual(recorded["source"], "agent")

    def test_a_bad_kind_from_an_agent_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            with self.assertRaises(notes.ClaimError):
                call_tool(store, "record_claim", {"type": "vibe", "text": "something"})


if __name__ == "__main__":
    unittest.main()
