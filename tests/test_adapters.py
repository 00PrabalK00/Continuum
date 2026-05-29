import tempfile
import unittest
from pathlib import Path

from continuum.adapters import (
    ClaudeTerminalAdapter,
    CodexTerminalAdapter,
    GeminiTerminalAdapter,
    terminal_adapter,
)


class TerminalAdapterTest(unittest.TestCase):
    def test_factory_returns_provider_specific_adapters(self):
        project = Path.cwd()
        self.assertIsInstance(terminal_adapter("claude", project), ClaudeTerminalAdapter)
        self.assertIsInstance(terminal_adapter("codex", project), CodexTerminalAdapter)
        self.assertIsInstance(terminal_adapter("gemini", project), GeminiTerminalAdapter)

    def test_claude_appends_initial_context_without_permission_bypass(self):
        args = ClaudeTerminalAdapter(Path.cwd()).prepare_args(["--model", "opus"], "read handoff")
        self.assertEqual(args, ["--model", "opus", "read handoff"])
        self.assertNotIn("--dangerously-skip-permissions", args)

    def test_codex_scopes_interactive_tui_to_project_unless_user_supplies_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            adapter = CodexTerminalAdapter(project)
            args = adapter.prepare_args(["--no-alt-screen"], "continue")
            self.assertEqual(args[:2], ["-C", str(project.resolve())])
            self.assertEqual(args[-1], "continue")
            supplied = adapter.prepare_args(["--cd", "elsewhere"], None)
            self.assertEqual(supplied, ["--no-alt-screen", "--cd", "elsewhere"])

    def test_gemini_uses_interactive_prompt_mode_and_does_not_enable_yolo(self):
        adapter = GeminiTerminalAdapter(Path.cwd())
        args = adapter.prepare_args(["--approval-mode", "default"], "read handoff")
        self.assertEqual(args[:3], ["--prompt-interactive", "read handoff", "--screen-reader"])
        self.assertNotIn("yolo", args)
        merged = adapter.prepare_args(["--prompt-interactive", "inspect tests"], "read handoff")
        self.assertIn("read handoff", merged[2])
        self.assertIn("inspect tests", merged[2])

    def test_adapter_detects_split_approval_prompt_once_and_tracks_completion(self):
        adapter = ClaudeTerminalAdapter(Path.cwd())
        started = adapter.feed("\x1b[1mDo you want to pro")
        self.assertEqual(started[0].phase, "WORKING")
        events = adapter.feed("ceed?\x1b[0m")
        self.assertEqual(events[0].phase, "WAITING_APPROVAL")
        self.assertEqual(adapter.state.approval_prompts, 1)
        self.assertFalse(adapter.feed("Do you want to proceed?"))
        event = adapter.finish(0)
        self.assertEqual(event.phase, "EXITED")

    def test_nonzero_terminal_exit_is_failed_status(self):
        adapter = CodexTerminalAdapter(Path.cwd())
        self.assertEqual(adapter.finish(1).phase, "FAILED")


if __name__ == "__main__":
    unittest.main()
