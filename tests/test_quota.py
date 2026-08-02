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
                                      evidence="usage limit reached", reset_at=future,
                                      confirmed=True)
            entry = quota.headroom(store, "claude")
            self.assertEqual(entry.state, "blocked")
            self.assertIn("resets at", entry.reason)

    def test_an_unconfirmed_mention_is_recorded_but_does_not_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            store.record_limit_signal(agent="claude", kind="exhausted",
                                      evidence="reading quota.py: usage limit reached",
                                      confirmed=False)
            entry = quota.headroom(store, "claude")
            self.assertEqual(entry.state, "unknown")
            self.assertEqual(entry.unconfirmed, 1)
            # Still visible, so a user can see why nothing was acted on.
            self.assertIn("usage limit reached", entry.evidence)

    def test_a_reset_the_agent_stated_outlives_the_usage_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            tomorrow = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()
            store.record_limit_signal(agent="claude", kind="exhausted",
                                      evidence="resets in 1 day", reset_at=tomorrow,
                                      confirmed=True)
            # A five hour window would have lost this, while the agent is still
            # blocked by its own account.
            entry = quota.headroom(store, "claude", window_hours=1)
            self.assertEqual(entry.state, "blocked")

    def test_an_expired_stated_reset_releases_the_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat()
            store.record_limit_signal(agent="claude", kind="exhausted",
                                      evidence="retry in 5 minutes", reset_at=past,
                                      confirmed=True)
            entry = quota.headroom(store, "claude")
            # The agent's own word beats a cooldown Continuum invented.
            self.assertEqual(entry.state, "recently_limited")
            self.assertNotIn("gave no reset time", entry.reason)

    def test_an_agent_with_headroom_is_preferred(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).isoformat()
            store.record_limit_signal(agent="claude", kind="exhausted",
                                      evidence="usage limit reached", reset_at=future,
                                      confirmed=True)
            order = [entry.agent for entry in quota.rank(store, ["claude", "codex"])]
            self.assertEqual(order[0], "codex")


class RoutingTest(unittest.TestCase):
    """Avoiding the agent that just ran is a preference. Avoiding one that said
    it is out is a fact, so the fact has to win."""

    def blocked(self, store, agent):
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).isoformat()
        store.record_limit_signal(agent=agent, kind="exhausted",
                                  evidence="usage limit reached", reset_at=future,
                                  confirmed=True)

    def choose(self, store, installed, last_agent):
        from unittest.mock import patch

        from continuum.cli import choose_agent

        def pick(_store, exclude=None):
            # Stands in for the real registry, which reads PATH and raises when
            # no agent CLI is installed. CI machines have none.
            return next((name for name in installed if name != exclude), installed[0])

        with (
            patch("continuum.cli.installed_agents", return_value=installed),
            patch("continuum.cli.last_session", return_value={"agent": last_agent}),
            patch("continuum.cli.pick_agent", side_effect=pick),
        ):
            return choose_agent(store)

    def test_the_blocked_agent_is_not_chosen_even_if_the_other_ran_last(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            self.blocked(store, "claude")
            agent, reason = self.choose(store, ["claude", "codex"], "codex")
            self.assertEqual(agent, "codex")
            self.assertIn("only agent left", reason)

    def test_a_free_agent_that_did_not_just_run_is_preferred(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            self.blocked(store, "claude")
            agent, _ = self.choose(store, ["claude", "codex", "gemini"], "codex")
            self.assertEqual(agent, "gemini")

    def test_with_nothing_reported_the_behaviour_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            agent, reason = self.choose(store, ["claude", "codex"], "claude")
            self.assertEqual(agent, "codex")
            self.assertIn("ran last session", reason)

    def test_every_agent_blocked_still_returns_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            self.blocked(store, "claude")
            self.blocked(store, "codex")
            agent, _ = self.choose(store, ["claude", "codex"], "claude")
            self.assertIn(agent, {"claude", "codex"})


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
    def test_the_limits_command_runs_with_no_agents_installed(self):
        from unittest.mock import patch

        from continuum.cli import main

        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with (
                patch("continuum.cli.installed_agents", return_value=[]),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["limits", "--project", str(project)]), 0)
            self.assertIn("No agent CLIs installed", output.getvalue())

    def test_the_limits_command_reports_a_known_agent(self):
        from unittest.mock import patch

        from continuum.cli import main

        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with (
                patch("continuum.cli.installed_agents", return_value=["claude"]),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["limits", "--project", str(project)]), 0)
            self.assertIn("claude", output.getvalue())

    def test_the_mcp_tool_reports_the_same_thing(self):
        from continuum.mcp_server import call_tool, tool_definitions

        names = [item["name"] for item in tool_definitions()]
        self.assertIn("get_agent_limits", names)
        tool = next(item for item in tool_definitions() if item["name"] == "get_agent_limits")
        self.assertIn("cannot report how much quota remains", tool["description"])
        # A clean machine has no agent CLIs, so the report must still carry the
        # disclaimer rather than depending on what happens to be on PATH.
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            with patch("continuum.agents.installed_agents", return_value=["claude"]):
                text = call_tool(store, "get_agent_limits", {})["content"][0]["text"]
            self.assertIn("no way to ask", text)
            with patch("continuum.agents.installed_agents", return_value=[]):
                empty = call_tool(store, "get_agent_limits", {})["content"][0]["text"]
            self.assertIn("no way to ask", empty)


if __name__ == "__main__":
    unittest.main()
