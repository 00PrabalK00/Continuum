import tempfile
import time
import unittest
import argparse
from contextlib import redirect_stderr, redirect_stdout
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

    def test_interactive_run_records_terminal_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with (
                patch("continuum.cli.agent_command", return_value=["agent"]),
                patch("continuum.cli.terminal_backend", return_value="pty"),
                patch("continuum.cli.run_terminal_process", side_effect=lambda *args, **kwargs: kwargs["on_output"]("ready\r\n") or 0),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["run", "--project", str(project), "--interactive", "codex"]), 0)
            usage = (project / ".continuum" / "token_usage.json").read_text(encoding="utf-8")
            self.assertIn('"terminal": "pty"', usage)
            self.assertIn('"adapter": "codex_interactive"', usage)
            self.assertIn('"adapter_phase": "EXITED"', usage)
            self.assertIn("Interactive terminal backend: pty", output.getvalue())
            self.assertIn("Interactive adapter: codex_interactive", output.getvalue())

    def test_adapters_list_describes_bounded_interactive_behavior(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["adapters", "list"]), 0)
        rendered = output.getvalue()
        self.assertIn("claude: claude_interactive", rendered)
        self.assertIn("codex: codex_interactive", rendered)
        self.assertIn("gemini: gemini_interactive", rendered)
        self.assertIn("never auto-approves", rendered)

    def test_worktree_resume_runs_agent_inside_isolated_worktree(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            import subprocess

            project.mkdir()
            subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
            (project / "src").mkdir()
            (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Continuum Test", "-c", "user.email=test@example.invalid", "commit", "-m", "base"],
                cwd=project,
                capture_output=True,
                check=True,
            )
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["init", "--project", str(project)]), 0)
                self.assertEqual(
                    main([
                        "worktree", "schedule", "--project", str(project),
                        "split work", "--lane", "backend:codex:src",
                    ]),
                    0,
                )
            captured = {}

            def fake_run(args, resumed=False, injected_context=None):
                captured["cwd"] = str(args.cwd)
                captured["resumed"] = resumed
                captured["context"] = injected_context
                return 0

            with patch("continuum.cli.run_agent", side_effect=fake_run):
                self.assertEqual(main(["worktree", "resume", "--project", str(project), "T0001", "codex"]), 0)
            self.assertIn(".continuum", captured["cwd"])
            self.assertTrue(captured["resumed"])
            self.assertIn("Parallel Worktree Context", captured["context"])

    def test_interactive_terminal_missing_backend_prints_specific_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            errors = StringIO()
            from continuum.terminal import TerminalUnavailable

            with (
                patch("continuum.cli.agent_command", return_value=["agent"]),
                patch("continuum.cli.terminal_backend", return_value="unavailable"),
                patch(
                    "continuum.cli.run_terminal_process",
                    side_effect=TerminalUnavailable(
                        "Interactive terminal mode on Windows requires pywinpty. "
                        "Run `py -m pip install pywinpty`, then retry with `--interactive`."
                    ),
                ),
                redirect_stderr(errors),
            ):
                self.assertEqual(main(["run", "--project", str(project), "--interactive", "codex"]), 1)
            self.assertIn("py -m pip install pywinpty", errors.getvalue())

    def test_session_commands_show_detected_and_published_external_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            attached = {
                "session": {"session_id": "S0001", "status": "ATTACHED", "agent": "claude", "pid": 42, "cwd": str(project)},
                "packet": {"path": str(project / ".continuum" / "external_sessions" / "S0001" / "context.md"), "estimated_tokens": 70, "mode": "compact"},
            }
            with (
                patch("continuum.cli.ExternalSessionManager.detect", return_value=[{"pid": 42, "agent": "claude", "project_match": True, "cwd": str(project)}]),
                patch("continuum.cli.ExternalSessionManager.attach", return_value=attached),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["session", "detect", "--project", str(project)]), 0)
                self.assertEqual(main(["session", "attach", "--project", str(project), "42"]), 0)
            rendered = output.getvalue()
            self.assertIn("PID=42 claude project-match", rendered)
            self.assertIn("Context packet published:", rendered)
            self.assertIn("cannot retroactively capture or type", rendered)


if __name__ == "__main__":
    unittest.main()
