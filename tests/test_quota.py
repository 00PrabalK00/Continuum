"""Quota tracking is only worth having if it never states something it cannot know.

Most of these tests are about what Continuum must refuse to say.
"""

import datetime as dt
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from continuum import quota
from continuum.core import MemoryStore, utc_now


def fresh(temporary: str) -> MemoryStore:
    store = MemoryStore(Path(temporary) / "repo")
    store.initialize(100000, 0.8)
    return store


class DetectionTest(unittest.TestCase):
    def test_it_recognises_how_agents_say_they_are_out(self):
        for line in (
            "Claude usage limit reached",
            "Error: rate limit exceeded",
            "429 Too Many Requests",
            "Your credit balance is too low",
            "RESOURCE_EXHAUSTED",
        ):
            self.assertTrue(quota.scan(line), line)

    def test_ordinary_output_is_not_a_limit(self):
        for line in ("retrying the request", "reading limits.py", "all tests passed"):
            self.assertEqual(quota.scan(line), [], line)

    def test_a_bare_429_is_not_enough(self):
        # It appears in ordinary output far too often to treat as evidence.
        self.assertEqual(quota.scan("processed 429 records"), [])

    def test_escape_sequences_do_not_hide_a_limit(self):
        self.assertTrue(quota.scan("\x1b[31musage limit reached\x1b[0m"))


class ResetParsingTest(unittest.TestCase):
    def test_a_relative_time_the_agent_stated_is_read(self):
        now = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
        parsed = quota.parse_reset("try again in 30 minutes", now)
        self.assertEqual(parsed, "2026-01-01T12:30:00+00:00")

    def test_hours_and_days_are_understood(self):
        now = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(quota.parse_reset("retry in 2 hours", now), "2026-01-01T14:00:00+00:00")
        self.assertEqual(quota.parse_reset("resets in 1 day", now), "2026-01-02T12:00:00+00:00")

    def test_nothing_is_invented_when_no_time_was_stated(self):
        # Extrapolating a reset from how long ago a limit was hit would be a
        # guess printed in the same place as a fact.
        self.assertIsNone(quota.parse_reset("usage limit reached"))
        self.assertIsNone(quota.parse_reset("rate limited, sorry"))

    def test_a_nonsense_clock_time_is_refused(self):
        self.assertIsNone(quota.parse_reset("resets at 99:99"))


class ConfirmationTest(unittest.TestCase):
    """An agent reading this repository prints these phrases. Matching one
    cannot mean the agent is blocked."""

    def test_a_single_mention_in_a_clean_session_is_not_confirmed(self):
        tracker = quota.SessionTracker("claude", "S1")
        tracker.observe("reading quota.py, which mentions usage limit reached")
        self.assertEqual(tracker.confirmed(0), [])

    def test_a_stated_reset_time_confirms_immediately(self):
        tracker = quota.SessionTracker("claude", "S1")
        tracker.observe("usage limit reached, try again in 30 minutes")
        self.assertTrue(tracker.confirmed(0))

    def test_repetition_confirms(self):
        tracker = quota.SessionTracker("claude", "S1")
        for index in range(3):
            tracker.observe(f"usage limit reached ({index})\n")
        self.assertTrue(tracker.confirmed(0))

    def test_a_failed_session_confirms(self):
        tracker = quota.SessionTracker("claude", "S1")
        tracker.observe("usage limit reached")
        self.assertEqual(tracker.confirmed(0), [])
        self.assertTrue(tracker.confirmed(1))

    def test_the_same_line_twice_is_recorded_once(self):
        tracker = quota.SessionTracker("claude", "S1")
        tracker.observe("usage limit reached, try again in 5 minutes")
        tracker.observe("usage limit reached, try again in 5 minutes")
        self.assertEqual(len(tracker.signals), 1)


class PersistenceTest(unittest.TestCase):
    def test_usage_accumulates_across_sessions(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            for index in range(3):
                store.record_agent_usage(
                    session=f"S{index}", agent="claude",
                    started_at=utc_now(), ended_at=utc_now(),
                    injected_tokens=100, output_tokens=200,
                )
            rows = store.agent_usage_since("2000-01-01T00:00:00+00:00", "claude")
            self.assertEqual(len(rows), 3)
            self.assertEqual(sum(row["estimated_tokens"] for row in rows), 900)

    def test_the_injected_prompt_counts_towards_the_total(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            store.record_agent_usage(session="S1", agent="claude", started_at=utc_now(),
                                     ended_at=utc_now(), injected_tokens=400, output_tokens=100)
            row = store.agent_usage_since("2000-01-01T00:00:00+00:00")[0]
            self.assertEqual(row["estimated_tokens"], 500)

    def test_evidence_is_stored_as_the_agent_wrote_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            store.record_limit_signal(agent="claude", kind="exhausted",
                                      evidence="Claude usage limit reached. resets at 3pm")
            row = store.limit_signals_since("2000-01-01T00:00:00+00:00")[0]
            self.assertEqual(row["evidence"], "Claude usage limit reached. resets at 3pm")

    def test_a_read_only_store_does_not_fail_the_session(self):
        import sqlite3
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            with patch.object(MemoryStore, "record_agent_usage",
                              side_effect=sqlite3.OperationalError("readonly")):
                quota.record_session(store, agent="claude", session="S1",
                                     started_at=utc_now(), ended_at=utc_now(),
                                     injected_tokens=1, output_tokens=1,
                                     estimate_quality="piped", checkpoint_triggered=False,
                                     returncode=0)


class HeadroomTest(unittest.TestCase):
    def test_an_agent_with_no_history_is_unknown_not_healthy(self):
        with tempfile.TemporaryDirectory() as temporary:
            entry = quota.headroom(fresh(temporary), "claude")
            self.assertEqual(entry.state, "unknown")

    def test_a_stated_future_reset_blocks_the_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).isoformat()
            store.record_limit_signal(agent="claude", kind="exhausted",
                                      evidence="usage limit reached", reset_at=future)
            entry = quota.headroom(store, "claude")
            self.assertEqual(entry.state, "blocked")
            self.assertIn("resets at", entry.reason)

    def test_an_agent_with_headroom_is_preferred(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).isoformat()
            store.record_limit_signal(agent="claude", kind="exhausted",
                                      evidence="usage limit reached", reset_at=future)
            order = [entry.agent for entry in quota.rank(store, ["claude", "codex"])]
            self.assertEqual(order[0], "codex")


class HonestyTest(unittest.TestCase):
    """The rendered view must not contain a figure Continuum cannot justify."""

    def render(self, entries):
        return quota.render(entries)

    def test_no_percentage_of_quota_is_ever_shown(self):
        text = self.render([
            quota.Headroom("claude", "blocked", "reported a limit 5m ago",
                           evidence="usage limit reached", sessions=3, estimated_tokens=180000),
            quota.Headroom("codex", "unknown", "no limit reported", sessions=1, estimated_tokens=24000),
        ])
        self.assertNotIn("%", text)
        self.assertNotIn("remaining", text.split("What these mean")[0])

    def test_estimates_are_labelled_as_estimates(self):
        text = self.render([quota.Headroom("claude", "unknown", "no limit reported")])
        self.assertIn("estimated", text)
        self.assertIn("four characters per token", text)

    def test_it_says_plainly_that_remaining_quota_is_unknown(self):
        text = self.render([quota.Headroom("claude", "unknown", "no limit reported")])
        self.assertIn("There is no way to ask", text)

    def test_an_unknown_agent_is_not_dressed_up_as_healthy(self):
        import re

        text = self.render([quota.Headroom("codex", "unknown", "no limit reported")])
        self.assertIn("no limit message recorded", text)
        body = text.split("What these mean")[0].lower()
        for word in ("healthy", "available", "ready", "fine", "good"):
            self.assertIsNone(
                re.search(rf"\b{word}\b", body),
                f"unknown standing should not read as {word}",
            )


class CommandTest(unittest.TestCase):
    def test_the_limits_command_runs(self):
        from continuum.cli import main

        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["limits", "--project", str(project)]), 0)
            self.assertTrue(output.getvalue())

    def test_the_mcp_tool_reports_the_same_thing(self):
        from continuum.mcp_server import call_tool, tool_definitions

        names = [item["name"] for item in tool_definitions()]
        self.assertIn("get_agent_limits", names)
        tool = next(item for item in tool_definitions() if item["name"] == "get_agent_limits")
        self.assertIn("cannot report how much quota remains", tool["description"])
        with tempfile.TemporaryDirectory() as temporary:
            text = call_tool(fresh(temporary), "get_agent_limits", {})["content"][0]["text"]
            self.assertIn("no way to ask", text)


if __name__ == "__main__":
    unittest.main()
