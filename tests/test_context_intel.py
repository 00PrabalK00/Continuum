import json
import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from continuum.cli import main
from continuum.context_intel import (
    diff_intel,
    extract_symbols,
    gather_context_intel,
    recent_commits,
    relevant_tests,
    score_intel,
)
from continuum.core import MemoryStore


PY_SAMPLE = '''\
"""Module docstring with a fake def in_string() that should be ignored."""
# def commented_out():
import os


def real_function(arg):
    return arg


async def async_handler():
    pass


class WidgetService:
    def method(self):  # nested, also captured as a def
        return 1


CONFIG_VALUE = {"k": 1}
'''

JS_SAMPLE = '''\
// function commentedFn() {}
export function exportedFn(a) { return a; }
function plainFn() {}
export const arrowHandler = (x) => x + 1;
class Renderer {}
'''


class ExtractSymbolsTest(unittest.TestCase):
    def test_python_symbols_found_and_comments_strings_ignored(self):
        symbols = extract_symbols(PY_SAMPLE)
        names = {s["name"] for s in symbols}
        self.assertIn("real_function", names)
        self.assertIn("async_handler", names)
        self.assertIn("WidgetService", names)
        self.assertIn("CONFIG_VALUE", names)
        # The commented-out def and the docstring "def" must not register.
        self.assertNotIn("commented_out", names)
        self.assertNotIn("in_string", names)

    def test_javascript_symbols_found(self):
        symbols = extract_symbols(JS_SAMPLE)
        names = {s["name"] for s in symbols}
        self.assertIn("exportedFn", names)
        self.assertIn("plainFn", names)
        self.assertIn("arrowHandler", names)
        self.assertIn("Renderer", names)
        self.assertNotIn("commentedFn", names)

    def test_extract_from_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text(PY_SAMPLE, encoding="utf-8")
            symbols = extract_symbols(path)
            self.assertIn("real_function", {s["name"] for s in symbols})

    def test_missing_path_returns_empty(self):
        self.assertEqual(extract_symbols(Path("does_not_exist.py")), [])


class ContextIntelGitTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        subprocess.run(["git", "init"], cwd=self.project, capture_output=True, check=True)
        # CI has no global identity; persist it on the temp repo.
        subprocess.run(["git", "config", "user.name", "Continuum Test"], cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.project, capture_output=True, check=True)
        (self.project / "login.py").write_text("def login(user):\n    return user\n", encoding="utf-8")
        tests_dir = self.project / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_login.py").write_text(
            "from login import login\n\ndef test_login():\n    assert login('a') == 'a'\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "add login and test"], cwd=self.project, capture_output=True, check=True)
        self.store = MemoryStore(self.project)
        self.store.initialize(1000, 0.8)

    def tearDown(self):
        self.temporary.cleanup()

    def test_relevant_tests_links_test_to_module(self):
        tests = relevant_tests(self.project, ["login.py"])
        self.assertIn("tests/test_login.py", tests)

    def test_recent_commits_in_git_repo(self):
        commits = recent_commits(self.project, ["login.py"], limit=5)
        self.assertTrue(commits)
        self.assertIn("add login and test", {c["subject"] for c in commits})
        self.assertTrue(all(c["sha"] for c in commits))

    def test_recent_commits_empty_in_non_git_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(recent_commits(Path(tmp), ["x.py"], limit=5), [])

    def test_gather_context_intel_for_task(self):
        task = self.store.create_task("Work on login", "sequential")
        self.store.claim_files(task["task_id"], "claude", ["login.py"])
        intel = gather_context_intel(self.store, task["task_id"])
        self.assertEqual(intel["task_id"], task["task_id"])
        self.assertIn("login.py", intel["files"])
        self.assertIn("login.py", intel["symbols"])
        self.assertIn("login", {s["name"] for s in intel["symbols"]["login.py"]})
        self.assertIn("tests/test_login.py", intel["tests"])
        self.assertTrue(intel["recent_commits"])

    def test_gather_context_intel_unknown_task_raises(self):
        with self.assertRaises(ValueError):
            gather_context_intel(self.store, "T9999")

    def test_diff_intel_reports_only_in_each(self):
        intel_a = gather_context_intel(self.store, files=["login.py"])
        intel_b = gather_context_intel(self.store, files=["tests/test_login.py"])
        diff = diff_intel(intel_a, intel_b)
        self.assertIn("login.py", diff["files_only_a"])
        self.assertIn("tests/test_login.py", diff["files_only_b"])
        self.assertIsInstance(diff["token_delta"], int)

    def test_score_intel_fields_and_missing_info(self):
        task = self.store.create_task("Work on login", "sequential")
        self.store.claim_files(task["task_id"], "claude", ["login.py"])
        score = score_intel(self.store, task["task_id"])
        self.assertEqual(score["task_id"], task["task_id"])
        self.assertGreater(score["estimated_tokens"], 0)
        self.assertGreaterEqual(score["source_count"], 1)
        self.assertIn(score["risk_level"], {"low", "med", "high"})
        self.assertIsInstance(score["missing_info"], list)

    def test_score_flags_missing_info_when_sections_empty(self):
        # A task with no claims and a clean tree: expect missing-info flags.
        # Commit the post-init bookkeeping files so the tree is clean (no changes).
        subprocess.run(["git", "add", "-A"], cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init bookkeeping"], cwd=self.project, capture_output=True, check=True)
        task = self.store.create_task("Empty task", "sequential")
        score = score_intel(self.store, task["task_id"])
        self.assertIn("no owned or changed files", score["missing_info"])
        self.assertIn("no tests linked", score["missing_info"])
        self.assertIn("no symbols extracted", score["missing_info"])


class ContextIntelCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        subprocess.run(["git", "init"], cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Continuum Test"], cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.project, capture_output=True, check=True)
        (self.project / "service.py").write_text("def serve():\n    return 1\n", encoding="utf-8")
        tests_dir = self.project / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_service.py").write_text("from service import serve\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init service"], cwd=self.project, capture_output=True, check=True)
        self.store = MemoryStore(self.project)
        self.store.initialize(1000, 0.8)
        self.task_a = self.store.create_task("Service work", "sequential")
        self.store.claim_files(self.task_a["task_id"], "claude", ["service.py"])
        self.task_b = self.store.create_task("Test work", "sequential")
        self.store.claim_files(self.task_b["task_id"], "codex", ["tests/test_service.py"])

    def tearDown(self):
        self.temporary.cleanup()

    def _run(self, argv):
        out = StringIO()
        with patch("sys.stdout", out):
            code = main(argv)
        return code, out.getvalue()

    def _base(self):
        return ["--project", str(self.project)]

    def test_enrich_human_and_json(self):
        code, text = self._run(["context", "enrich", self.task_a["task_id"], *self._base()])
        self.assertEqual(code, 0)
        self.assertIn("Context Intel", text)
        self.assertIn("service.py", text)

        code, text = self._run(["context", "enrich", self.task_a["task_id"], "--json", *self._base()])
        self.assertEqual(code, 0)
        record = json.loads(text)
        self.assertEqual(record["task_id"], self.task_a["task_id"])
        self.assertIn("service.py", record["files"])
        self.assertIn("symbols", record)
        self.assertIn("tests", record)

    def test_enrich_unknown_task_exit_nonzero(self):
        code, _ = self._run(["context", "enrich", "T9999", *self._base()])
        self.assertEqual(code, 1)

    def test_diff_json_shape(self):
        code, text = self._run([
            "context", "diff", self.task_a["task_id"], self.task_b["task_id"], "--json", *self._base()
        ])
        self.assertEqual(code, 0)
        diff = json.loads(text)
        for key in ("files_only_a", "files_only_b", "symbols_only_a", "tests_only_a", "token_delta"):
            self.assertIn(key, diff)
        self.assertIn("service.py", diff["files_only_a"])
        self.assertIn("tests/test_service.py", diff["files_only_b"])

    def test_score_json_shape_and_exit(self):
        code, text = self._run(["context", "score", self.task_a["task_id"], "--json", *self._base()])
        self.assertEqual(code, 0)
        score = json.loads(text)
        for key in ("estimated_tokens", "source_count", "staleness_hours", "risk_level", "missing_info"):
            self.assertIn(key, score)
        self.assertIn(score["risk_level"], {"low", "med", "high"})

    def test_score_unknown_task_exit_nonzero(self):
        code, _ = self._run(["context", "score", "T9999", *self._base()])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
