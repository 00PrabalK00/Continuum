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

    def test_switch_selects_agent_and_resumes_with_context(self):
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

            self.assertEqual(shell.execute("/switch claude deep --model claude-opus-4-1-20250805"), 0)

            self.assertEqual(
                calls[0],
                [
                    "resume",
                    "--project",
                    str(Path(temporary).resolve()),
                    "claude",
                    "deep",
                    "--model",
                    "claude-opus-4-1-20250805",
                ],
            )
            self.assertIn("\033[35mclaude", shell.prompt())

    def test_bare_slash_shows_available_commands(self):
        output = StringIO()
        calls = []
        shell = InteractiveShell(Path("."), None, dispatch=lambda argv: calls.append(argv) or 0, output=output)

        self.assertEqual(shell.execute("/"), 0)

        rendered = output.getvalue()
        self.assertIn("Slash commands:", rendered)
        self.assertIn("/switch agent [mode]", rendered)
        self.assertIn("/status [--events]", rendered)
        self.assertEqual(calls, [])

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

    def test_instruct_slash_command_uses_key_value_contract_syntax(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            shell = InteractiveShell(Path(temporary), None, dispatch=lambda argv: calls.append(argv) or 0)

            self.assertEqual(
                shell.execute('/instruct planner=claude-opus-4-1-20250805 executor=codex mode=checkpoint scope=continuum/terminal.py,tests/test_terminal.py goal="Implement PTY receipts"'),
                0,
            )

            project = str(Path(temporary).resolve())
            self.assertEqual(
                calls[0],
                [
                    "instruct",
                    "--project",
                    project,
                    "--planner",
                    "claude-opus-4-1-20250805",
                    "--executor",
                    "codex",
                    "--goal",
                    "Implement PTY receipts",
                    "--mode",
                    "checkpoint",
                    "--scope",
                    "continuum/terminal.py",
                    "--scope",
                    "tests/test_terminal.py",
                ],
            )

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

    def test_any_cli_command_can_be_used_as_slash_command_with_project_injected(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            project = Path(temporary)
            shell = InteractiveShell(project, None, dispatch=lambda argv: calls.append(argv) or 0, animation="off")

            self.assertEqual(shell.execute('/handoff --task "Fix auth" --next-step "Run tests"'), 0)
            self.assertEqual(shell.execute('/instruct --planner claude-opus-4-1-20250805 --executor codex --goal "Fix tests"'), 0)
            self.assertEqual(shell.execute("/mcp serve"), 0)
            self.assertEqual(shell.execute("/adapters list"), 0)
            self.assertEqual(shell.execute("/context build coder --mode compact"), 0)
            self.assertEqual(shell.execute("/run --interactive codex"), 0)
            self.assertEqual(shell.execute("/resume --interactive gemini normal"), 0)
            self.assertEqual(shell.execute('/route explain "fix auth"'), 0)
            self.assertEqual(shell.execute("/ui --open"), 0)

            expected_project = str(project.resolve())
            self.assertEqual(
                calls[0],
                ["handoff", "--project", expected_project, "--task", "Fix auth", "--next-step", "Run tests"],
            )
            self.assertEqual(
                calls[1],
                [
                    "instruct",
                    "--project",
                    expected_project,
                    "--planner",
                    "claude-opus-4-1-20250805",
                    "--executor",
                    "codex",
                    "--goal",
                    "Fix tests",
                ],
            )
            self.assertEqual(calls[2], ["mcp", "--project", expected_project, "serve"])
            self.assertEqual(calls[3], ["adapters", "list", "--project", expected_project])
            self.assertEqual(calls[4], ["context", "build", "--project", expected_project, "coder", "--mode", "compact"])
            self.assertEqual(calls[5], ["run", "--project", expected_project, "--interactive", "codex"])
            self.assertEqual(calls[6], ["resume", "--project", expected_project, "--interactive", "gemini", "normal"])
            self.assertEqual(calls[7], ["route", "explain", "--project", expected_project, "fix auth"])
            self.assertEqual(calls[8], ["ui", "--project", expected_project, "--open"])

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
