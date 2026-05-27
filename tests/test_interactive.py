import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from continuum.cli import main
from continuum.interactive import InteractiveShell


class InteractiveShellTest(unittest.TestCase):
    def test_agent_selection_colors_prompt_and_routes_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            output = StringIO()
            shell = InteractiveShell(
                Path(temporary),
                None,
                dispatch=lambda argv: calls.append(argv) or 0,
                color="always",
                animation="off",
                output=output,
            )

            self.assertEqual(shell.execute("/agent gemini"), 0)
            self.assertEqual(shell.execute("/resume normal"), 0)

            self.assertEqual(calls[0], ["resume", "--project", str(Path(temporary).resolve()), "gemini", "normal"])
            self.assertIn("\033[34mgemini", shell.prompt())

    def test_terminal_routes_to_interactive_pty_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            shell = InteractiveShell(Path(temporary), None, dispatch=lambda argv: calls.append(argv) or 0)

            self.assertEqual(shell.execute("/terminal codex --model strong"), 0)
            self.assertEqual(shell.execute("/resume-terminal gemini normal"), 0)

            project = str(Path(temporary).resolve())
            self.assertEqual(calls[0], ["run", "--project", project, "--interactive", "codex", "--model", "strong"])
            self.assertEqual(calls[1], ["resume", "--project", project, "--interactive", "gemini", "normal"])

    def test_session_route_defaults_to_attached_session_list(self):
        calls = []
        shell = InteractiveShell(Path("."), None, dispatch=lambda argv: calls.append(argv) or 0)

        self.assertEqual(shell.execute("/sessions"), 0)

        self.assertEqual(calls[0][0:2], ["session", "list"])

    def test_slash_commands_scope_project_and_preserve_semantic_flag(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            project = Path(temporary)
            shell = InteractiveShell(project, None, dispatch=lambda argv: calls.append(argv) or 0, animation="off")

            self.assertEqual(shell.execute('/handoff fix auth | run failing test'), 0)
            self.assertEqual(shell.execute('/memory auth callback --semantic'), 0)
            self.assertEqual(shell.execute('/task create "Fix auth"'), 0)

            expected_project = str(project.resolve())
            self.assertEqual(
                calls[0],
                ["handoff", "--project", expected_project, "--task", "fix auth", "--next-step", "run failing test"],
            )
            self.assertEqual(
                calls[1],
                ["memory", "retrieve", "--project", expected_project, "auth callback", "--semantic"],
            )
            self.assertEqual(calls[2], ["task", "create", "--project", expected_project, "Fix auth"])

    def test_terminal_legend_and_mcp_guidance_do_not_launch_processes(self):
        output = StringIO()
        calls = []
        with patch("continuum.interactive.shutil.which", return_value=None):
            shell = InteractiveShell(Path("."), None, dispatch=lambda argv: calls.append(argv) or 0, output=output)
            shell.terminals()
            self.assertEqual(shell.execute("/mcp"), 0)

        rendered = output.getvalue()
        self.assertIn("claude: not found", rendered)
        self.assertIn("ollama: model/embedding provider", rendered)
        self.assertIn("continuum mcp serve --project", rendered)
        self.assertEqual(calls, [])

    def test_incomplete_slash_command_prints_usage_without_unknown_error(self):
        output = StringIO()
        shell = InteractiveShell(Path("."), None, dispatch=lambda argv: 0, output=output)

        self.assertEqual(shell.execute("/task"), 0)

        self.assertIn("Usage: /task <subcommand>", output.getvalue())
        self.assertNotIn("Unknown command", output.getvalue())

    def test_shell_cli_command_starts_interactive_console(self):
        with patch("continuum.interactive.InteractiveShell.run", return_value=0) as run:
            self.assertEqual(main(["shell", "--project", ".", "--color", "never", "--animation", "off"]), 0)
        run.assert_called_once()

    def test_invalid_slash_arguments_do_not_exit_the_console(self):
        errors = StringIO()
        with patch("builtins.input", side_effect=["/task create", "/quit"]):
            with redirect_stderr(errors):
                self.assertEqual(main(["shell", "--color", "never", "--animation", "off"]), 0)
        self.assertIn("required: title", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
