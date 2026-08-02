"""Context that cannot go stale loudly is context that quietly lies.

Continuum's own current.md spent five merged pull requests telling every agent
to review a pull request that had already merged. Nothing was wrong with the
recording; the problem was that a claim about a past moment reads as a claim
about now. These tests cover recording the moment and reporting the drift.
"""

import argparse
import os
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from continuum import freshness
from continuum.cli import main, quick_status
from continuum.core import MemoryStore
from continuum.progress import record_progress


def git(project: Path, *args: str) -> str:
    finished = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True
    )
    return finished.stdout.strip()


def repo(temporary: str) -> MemoryStore:
    project = Path(temporary) / "repo"
    project.mkdir()
    git(project, "init")
    git(project, "config", "user.email", "t@example.com")
    git(project, "config", "user.name", "Test")
    store = MemoryStore(project)
    store.initialize(100000, 0.8)
    return store


def commit(store: MemoryStore, name: str) -> str:
    (store.project / name).write_text(name, encoding="utf-8")
    git(store.project, "add", name)
    git(store.project, "commit", "-m", name)
    return git(store.project, "rev-parse", "HEAD")


class RecordingTest(unittest.TestCase):
    def test_a_handoff_records_the_commit_it_was_written_against(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = repo(temporary)
            sha = commit(store, "a.py")
            record_progress(store, "did a thing", "do the next thing")
            self.assertEqual(store.recent_handoffs(1)[0]["payload"]["commit"], sha)

    def test_a_project_outside_git_records_no_commit_rather_than_a_fake_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "plain")
            store.initialize(100000, 0.8)
            record_progress(store, "did a thing", "do the next thing")
            self.assertIsNone(store.recent_handoffs(1)[0]["payload"]["commit"])


class DescribeTest(unittest.TestCase):
    def test_nothing_is_said_while_the_project_is_on_the_recorded_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = repo(temporary)
            commit(store, "a.py")
            record_progress(store, "did a thing", "do the next thing")
            self.assertIsNone(freshness.describe(store))

    def test_commits_since_the_handoff_are_counted(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = repo(temporary)
            commit(store, "a.py")
            record_progress(store, "did a thing", "do the next thing")
            commit(store, "b.py")
            commit(store, "c.py")
            drift = freshness.describe(store)
            self.assertIn("2 commits ago", drift)

    def test_one_commit_is_not_described_as_commits(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = repo(temporary)
            commit(store, "a.py")
            record_progress(store, "did a thing", "do the next thing")
            commit(store, "b.py")
            self.assertIn("1 commit ago", freshness.describe(store))

    def test_a_handoff_with_no_recorded_commit_claims_nothing(self):
        # Handoffs written before this existed have no commit. Reporting them
        # as current would be the same fault in the other direction.
        with tempfile.TemporaryDirectory() as temporary:
            store = repo(temporary)
            commit(store, "a.py")
            store.event("handoff", {"task": "old", "next_step": "older"})
            self.assertIsNone(freshness.describe(store))

    def test_a_project_outside_git_claims_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "plain")
            store.initialize(100000, 0.8)
            record_progress(store, "did a thing", "do the next thing")
            self.assertIsNone(freshness.describe(store))

    def test_a_rewound_branch_is_reported_rather_than_counted(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = repo(temporary)
            first = commit(store, "a.py")
            commit(store, "b.py")
            record_progress(store, "did a thing", "do the next thing")
            git(store.project, "reset", "--hard", first)
            self.assertIn("not an ancestor", freshness.describe(store))

    def test_a_sibling_branch_is_not_reported_as_ordinary_progress(self):
        # `recorded..HEAD` counts commits reachable from HEAD and not from the
        # recorded one, which is positive on a sibling branch even though HEAD
        # never descended from it. Counting those would describe a divergence
        # as progress.
        with tempfile.TemporaryDirectory() as temporary:
            store = repo(temporary)
            base = commit(store, "a.py")
            commit(store, "b.py")
            record_progress(store, "did a thing", "do the next thing")
            git(store.project, "checkout", "-q", "-b", "sibling", base)
            commit(store, "c.py")
            commit(store, "d.py")
            drift = freshness.describe(store)
            self.assertIn("not an ancestor", drift)
            self.assertNotIn("commits ago", drift)


class EveryPathTest(unittest.TestCase):
    """A handoff written by any route has to carry its commit. Only the shared
    record_progress path did, so the everyday `continuum go` session ending, the
    session-end hook and `continuum handoff` all recorded none, and the newest
    handoff is the one that gets compared."""

    def test_the_session_end_path_records_the_commit(self):
        from continuum.cli import finalize_handoff

        with tempfile.TemporaryDirectory() as temporary:
            store = repo(temporary)
            sha = commit(store, "a.py")
            with redirect_stdout(StringIO()):
                finalize_handoff(store, "claude", "S1", ["out"], 0, False)
            self.assertEqual(store.recent_handoffs(1)[0]["payload"].get("commit"), sha)

    def test_the_hook_path_records_the_commit(self):
        import argparse as argparse_module

        from continuum.cli import hook_session_end

        with tempfile.TemporaryDirectory() as temporary:
            store = repo(temporary)
            sha = commit(store, "a.py")
            record_progress(store, "did a thing", "do the next thing")
            with redirect_stdout(StringIO()):
                hook_session_end(argparse_module.Namespace(project=str(store.project), vault=None))
            self.assertEqual(store.recent_handoffs(1)[0]["payload"].get("commit"), sha)

    def test_the_explicit_handoff_command_records_the_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = repo(temporary)
            sha = commit(store, "a.py")
            with redirect_stdout(StringIO()):
                main(["handoff", "--project", str(store.project),
                      "--task", "t", "--next-step", "n"])
            self.assertEqual(store.recent_handoffs(1)[0]["payload"].get("commit"), sha)


class McpTest(unittest.TestCase):
    """Reading through MCP is the recommended path, so a warning that reaches
    only the CLI misses the common case."""

    def drifted(self, temporary):
        store = repo(temporary)
        commit(store, "a.py")
        record_progress(store, "did a thing", "do the next thing")
        commit(store, "b.py")
        return store

    def test_every_context_tool_carries_the_warning(self):
        from continuum.mcp_server import call_tool

        with tempfile.TemporaryDirectory() as temporary:
            store = self.drifted(temporary)
            for tool in ("get_startup_context", "get_current_state", "get_latest_handoff"):
                text = call_tool(store, tool, {})["content"][0]["text"]
                self.assertIn("1 commit ago", text, tool)

    def test_current_context_is_not_prefixed(self):
        from continuum.mcp_server import call_tool

        with tempfile.TemporaryDirectory() as temporary:
            store = repo(temporary)
            commit(store, "a.py")
            record_progress(store, "did a thing", "do the next thing")
            text = call_tool(store, "get_startup_context", {})["content"][0]["text"]
            self.assertNotIn("Recorded against commit", text)


class SurfacingTest(unittest.TestCase):
    """A warning nothing reads is not a warning."""

    def drifted(self, temporary):
        store = repo(temporary)
        commit(store, "a.py")
        record_progress(store, "did a thing", "do the next thing")
        commit(store, "b.py")
        return store

    def test_the_status_card_says_so(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.drifted(temporary)
            out = StringIO()
            with redirect_stdout(out):
                quick_status(argparse.Namespace(project=str(store.project), vault=None))
            self.assertIn("1 commit ago", out.getvalue())

    def test_the_injected_context_says_so(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.drifted(temporary)
            for mode in ("compact", "normal", "deep"):
                self.assertIn("1 commit ago", store.resume_context(mode), mode)

    def test_the_age_reaches_the_agent_even_without_git(self):
        # The status card shows an age because a person can read it. An agent
        # gets present-tense prose and no timestamp at all.
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "plain")
            store.initialize(100000, 0.8)
            record_progress(store, "did a thing", "do the next thing")
            handoff = store.state_dir / "latest_handoff.md"
            old = time.time() - 40 * 86_400
            os.utime(handoff, (old, old))
            self.assertIn("Recorded 40 days ago", store.resume_context("compact"))

    def test_todays_context_carries_no_age_note(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "plain")
            store.initialize(100000, 0.8)
            record_progress(store, "did a thing", "do the next thing")
            self.assertNotIn("Recorded", store.resume_context("compact"))

    def test_one_day_is_not_described_as_days(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "plain")
            store.initialize(100000, 0.8)
            record_progress(store, "did a thing", "do the next thing")
            handoff = store.state_dir / "latest_handoff.md"
            old = time.time() - 1.5 * 86_400
            os.utime(handoff, (old, old))
            self.assertIn("Recorded 1 day ago", store.resume_context("compact"))

    def test_current_context_is_not_prefixed_with_a_warning(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = repo(temporary)
            commit(store, "a.py")
            record_progress(store, "did a thing", "do the next thing")
            self.assertNotIn("Recorded against commit", store.resume_context("compact"))


if __name__ == "__main__":
    unittest.main()
