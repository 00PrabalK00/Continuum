import tempfile
import time
import unittest
import argparse
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from continuum.cli import down, main, pid_is_running, up


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
            self.assertIn("Automatic provider launching is not enabled in this version.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
