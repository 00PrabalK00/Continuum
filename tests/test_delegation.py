import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from continuum import agents
from continuum.cli import main
from continuum.core import MemoryStore
from continuum.delegation import (
    DelegationError,
    ask,
    build_prompt,
    clean_reply,
    failure_detail,
    stall_reason,
)
from continuum.mcp_server import call_tool, tool_definitions

# Stands in for a real agent CLI: reads the prompt from stdin or the last
# argument, then reports what it could see.
RESPONDER = (
    "import sys\n"
    "piped = '' if sys.stdin.isatty() else sys.stdin.read()\n"
    "tail = sys.argv[-1] if len(sys.argv) > 1 else ''\n"
    "prompt = piped or tail\n"
    "print('SEEN_CONTEXT', 'renamed the payment client' in prompt or 'latest_handoff' in prompt)\n"
    "print('SEEN_REQUEST', 'ship the release' in prompt)\n"
)


def responder_command(_spec=None, passthrough=None):
    """Stand in for agent_command, keeping the arguments the agent would get."""
    return [sys.executable, "-c", RESPONDER, *(passthrough or [])]


class DelegationTest(unittest.TestCase):
    def store(self, temporary: str) -> MemoryStore:
        store = MemoryStore(Path(temporary) / "repo")
        store.initialize(100000, 0.8)
        store.write_handoff("renamed the payment client", "fix the failing retry test")
        return store

    def test_another_agent_receives_context_and_the_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            with patch("continuum.cli.agent_command", side_effect=responder_command):
                result = ask(store, "claude", "ship the release", sender="codex")
            self.assertIn("SEEN_CONTEXT True", result["reply"])
            self.assertIn("SEEN_REQUEST True", result["reply"])
            self.assertEqual(result["returncode"], 0)

    def test_the_exchange_is_readable_from_both_sides(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            with patch("continuum.cli.agent_command", side_effect=responder_command):
                ask(store, "claude", "ship the release", sender="codex")
            to_claude = [item["body"] for item in store.messages("claude", None, 5)]
            to_codex = [item["body"] for item in store.messages("codex", None, 5)]
            self.assertIn("ship the release", to_claude)
            self.assertTrue(any("SEEN_CONTEXT" in body for body in to_codex))

    def test_stdin_agents_are_fed_through_stdin(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            agents.write_agent(store, "piped", {"inject": "stdin"})
            with patch("continuum.cli.agent_command", side_effect=responder_command):
                result = ask(store, "piped", "ship the release")
            self.assertIn("SEEN_REQUEST True", result["reply"])

    def test_shim_agents_get_a_single_line_prompt(self):
        captured: dict[str, list[str]] = {}

        def record(spec, passthrough):
            captured["args"] = list(passthrough)
            return responder_command()

        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            with (
                patch("continuum.cli.agent_command", side_effect=record),
                patch("continuum.cli.launches_through_shell", return_value=True),
            ):
                ask(store, "claude", "ship the release")
            for value in captured["args"]:
                self.assertNotIn("\n", value)
            self.assertTrue(any("ship the release" in value for value in captured["args"]))

    def test_an_empty_request_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(DelegationError):
                ask(self.store(temporary), "claude", "   ")

    def test_an_unreachable_agent_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            with patch("continuum.agents.shutil.which", return_value=None):
                with self.assertRaises(DelegationError):
                    ask(store, "nosuch", "ship the release")

    def test_a_silent_agent_is_not_treated_as_an_answer(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            with patch("continuum.cli.agent_command", return_value=[sys.executable, "-c", "pass"]):
                with self.assertRaises(DelegationError):
                    ask(store, "claude", "ship the release")

    def test_a_hanging_agent_times_out(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            script = "import time; time.sleep(30)"
            with patch("continuum.cli.agent_command", return_value=[sys.executable, "-c", script]):
                with self.assertRaises(DelegationError) as caught:
                    ask(store, "claude", "ship the release", timeout=2)
            self.assertIn("within 2 seconds", str(caught.exception))

    def test_an_agent_waiting_on_sign_in_is_reported_as_such(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            script = (
                "import sys, time\n"
                "print('Opening authentication page in your browser. "
                "Do you want to continue? [Y/n]:', flush=True)\n"
                "time.sleep(30)\n"
            )
            with patch("continuum.cli.agent_command", return_value=[sys.executable, "-c", script]):
                with self.assertRaises(DelegationError) as caught:
                    ask(store, "claude", "ship the release", timeout=3)
            message = str(caught.exception)
            self.assertIn("stopped to ask something", message)
            self.assertIn("finish signing in", message)

    def test_stall_reason_only_fires_on_a_question(self):
        self.assertIsNone(stall_reason("working on it\nstill working"))
        self.assertEqual(stall_reason("hi\nContinue? [Y/n]:"), "Continue? [Y/n]:")

    def test_failure_detail_skips_banner_noise(self):
        stderr = "Reading additional input from stdin...\nNot inside a trusted directory."
        self.assertEqual(failure_detail(stderr, 1), "Not inside a trusted directory.")

    def test_clean_reply_trims_padding(self):
        self.assertEqual(clean_reply("\n\n  answer  \n\n"), "answer")

    def test_prompt_tells_the_agent_it_is_being_consulted(self):
        with tempfile.TemporaryDirectory() as temporary:
            prompt = build_prompt(self.store(temporary), "codex", "ship the release", "compact")
            self.assertIn("consulted by another AI agent", prompt)
            self.assertIn("Calling agent: codex", prompt)
            self.assertIn("ship the release", prompt)


class DelegationMcpTest(unittest.TestCase):
    def test_the_delegation_tools_are_advertised(self):
        names = [tool["name"] for tool in tool_definitions()]
        self.assertIn("ask_agent", names)
        self.assertIn("list_agents", names)

    def test_list_agents_reports_availability(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "repo")
            store.initialize(100000, 0.8)
            with patch("continuum.agents.shutil.which", return_value=None):
                text = call_tool(store, "list_agents", {})["content"][0]["text"]
            self.assertIn("claude: not installed", text)
            self.assertIn("cannot reach any of them", text)

    def test_ask_agent_returns_the_other_agents_reply(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "repo")
            store.initialize(100000, 0.8)
            store.write_handoff("renamed the payment client", "fix the failing retry test")
            with patch("continuum.cli.agent_command", side_effect=responder_command):
                text = call_tool(
                    store,
                    "ask_agent",
                    {"agent": "claude", "request": "ship the release", "sender": "codex"},
                )["content"][0]["text"]
            self.assertIn("Reply from claude", text)
            self.assertIn("SEEN_REQUEST True", text)

    def test_ask_agent_failures_surface_as_tool_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "repo")
            store.initialize(100000, 0.8)
            with patch("continuum.agents.shutil.which", return_value=None):
                with self.assertRaises(ValueError):
                    call_tool(store, "ask_agent", {"agent": "nosuch", "request": "hi"})


class AskCommandTest(unittest.TestCase):
    def test_ask_prints_the_reply(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            store = MemoryStore(project)
            store.initialize(100000, 0.8)
            store.write_handoff("renamed the payment client", "fix the failing retry test")
            output = StringIO()
            with (
                patch("continuum.cli.agent_command", side_effect=responder_command),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["ask", "claude", "ship", "the", "release", "--project", str(project)]), 0)
            self.assertIn("Reply from claude", output.getvalue())


if __name__ == "__main__":
    unittest.main()
