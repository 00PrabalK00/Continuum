import tempfile
import time
import unittest
import argparse
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from continuum.cli import down, injected_resume_args, main, pid_is_running, up
from continuum.providers import ProviderError, ProviderManager


class CliTest(unittest.TestCase):
    def test_init_handoff_status_and_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            vault = Path(temporary) / "vault"
            output = StringIO()
            with redirect_stdout(output):
                import sys

                previous = sys.argv
                try:
                    sys.argv = ["continuum", "init", "--project", str(project), "--vault", str(vault)]
                    self.assertEqual(main(), 0)
                    sys.argv = [
                        "continuum",
                        "handoff",
                        "--project",
                        str(project),
                        "--task",
                        "debug build error",
                        "--next-step",
                        "run tests",
                    ]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "search", "--project", str(project), "build error"]
                    self.assertEqual(main(), 0)
                finally:
                    sys.argv = previous
            self.assertIn("debug build error", output.getvalue())

    def test_up_and_down_control_background_daemon(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            args = argparse.Namespace(project=str(project), vault=None)

            self.assertEqual(up(args), 0)
            pid = int((project / ".continuum" / "daemon.pid").read_text(encoding="utf-8"))
            time.sleep(0.1)
            self.assertTrue(pid_is_running(pid))
            self.assertEqual(down(args), 0)

    def test_task_commands_create_claim_and_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                import sys

                previous = sys.argv
                try:
                    sys.argv = ["continuum", "init", "--project", str(project)]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "task", "create", "--project", str(project), "Fix auth"]
                    self.assertEqual(main(), 0)
                    sys.argv = [
                        "continuum", "task", "claim", "--project", str(project), "T0001", "codex", "auth.py"
                    ]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "task", "complete", "--project", str(project), "T0001", "--summary", "Fixed."]
                    self.assertEqual(main(), 0)
                finally:
                    sys.argv = previous
            self.assertIn("T0001 DONE", output.getvalue())

    def test_init_does_not_select_team(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            import sys

            previous = sys.argv
            try:
                sys.argv = ["continuum", "init", "--project", str(project)]
                self.assertEqual(main(), 0)
            finally:
                sys.argv = previous
            self.assertFalse((project / ".continuum" / "teams").exists())

    def test_team_init_and_route_explain(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                import sys

                previous = sys.argv
                try:
                    sys.argv = ["continuum", "init", "--project", str(project)]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "team", "init", "--project", str(project), "default_dev_team"]
                    self.assertEqual(main(), 0)
                    sys.argv = [
                        "continuum", "route", "explain", "--project", str(project),
                        "fix failing auth test",
                    ]
                    self.assertEqual(main(), 0)
                finally:
                    sys.argv = previous
            self.assertIn("Classified route: test_repair", output.getvalue())

    def test_status_reports_release_health_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                import sys

                previous = sys.argv
                try:
                    sys.argv = ["continuum", "init", "--project", str(project)]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "status", "--project", str(project)]
                    self.assertEqual(main(), 0)
                finally:
                    sys.argv = previous
            rendered = output.getvalue()
            for label in ("Project path:", "Daemon state:", "SQLite state:", "MCP availability:", "Configured providers:", "Embedding count:", "Latest handoff path:"):
                self.assertIn(label, rendered)

    def test_team_run_output_is_explicitly_planning_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                import sys

                previous = sys.argv
                try:
                    sys.argv = ["continuum", "init", "--project", str(project)]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "team", "init", "--project", str(project), "fast_bugfix"]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "team", "run", "--project", str(project), "fast_bugfix", "fix login crash"]
                    self.assertEqual(main(), 0)
                finally:
                    sys.argv = previous
            self.assertIn("Automatic provider launching was not requested.", output.getvalue())
            self.assertIn("Workflow planned: W0001.", output.getvalue())

    def test_context_and_message_commands_are_bounded_and_visible(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                import sys

                previous = sys.argv
                try:
                    sys.argv = ["continuum", "init", "--project", str(project)]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "message", "send", "--project", str(project), "explorer", "coder", "check auth"]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "context", "build", "--project", str(project), "coder", "--mode", "compact"]
                    self.assertEqual(main(), 0)
                finally:
                    sys.argv = previous
            rendered = output.getvalue()
            self.assertIn("MSG0001", rendered)
            self.assertIn("Estimated context:", rendered)
            self.assertIn("check auth", rendered)

    def test_resume_injects_context_with_agent_specific_prompt_mode(self):
        prompt = "continue from handoff"

        self.assertEqual(injected_resume_args("claude", ["--model", "opus"], prompt)[-1], prompt)
        self.assertEqual(injected_resume_args("codex", [], prompt), [prompt])
        self.assertEqual(injected_resume_args("gemini", ["--approval-mode", "plan"], prompt)[:2], ["--prompt-interactive", prompt])
        merged = injected_resume_args("gemini", ["--prompt", "other"], prompt)
        self.assertIn(prompt, merged[1])
        self.assertIn("other", merged[1])

    def test_semantic_retrieval_falls_back_to_exact_search_when_ollama_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            import sys

            previous = sys.argv
            try:
                sys.argv = ["continuum", "init", "--project", str(project)]
                self.assertEqual(main(), 0)
                from continuum.core import MemoryStore

                MemoryStore(project).event("decision", {"summary": "auth retry behavior"})
                with patch.object(ProviderManager, "embed", side_effect=ProviderError("connection refused")):
                    with redirect_stdout(output):
                        sys.argv = ["continuum", "memory", "retrieve", "--project", str(project), "auth", "--semantic"]
                        self.assertEqual(main(), 0)
            finally:
                sys.argv = previous
            self.assertIn("Falling back to exact local search", output.getvalue())
            self.assertIn("auth retry behavior", output.getvalue())

    def test_memory_refresh_embeds_recent_events_with_event_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            import sys

            previous = sys.argv
            try:
                sys.argv = ["continuum", "init", "--project", str(project)]
                self.assertEqual(main(), 0)
                with patch.object(ProviderManager, "embed", return_value=("embed", [1.0, 0.0])):
                    sys.argv = ["continuum", "memory", "refresh", "--project", str(project), "--limit", "1"]
                    self.assertEqual(main(), 0)
            finally:
                sys.argv = previous
            from continuum.core import MemoryStore

            self.assertTrue(MemoryStore(project).semantic_search([1.0, 0.0])[0]["memory_id"].startswith("M"))


if __name__ == "__main__":
    unittest.main()
