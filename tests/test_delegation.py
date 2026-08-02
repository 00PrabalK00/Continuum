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


class ReadOnlyMemoryTest(unittest.TestCase):
    """Codex sandboxes its MCP servers read-only, so Continuum's store cannot be
    written from inside one. Consulting another agent must still work there."""

    def test_delegation_succeeds_when_memory_cannot_be_written(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "repo")
            store.initialize(100000, 0.8)
            store.write_handoff("renamed the payment client", "fix the failing retry test")

            def readonly(*_args, **_kwargs):
                raise sqlite3.OperationalError("attempt to write a readonly database")

            with (
                patch("continuum.cli.agent_command", side_effect=responder_command),
                patch.object(MemoryStore, "event", readonly),
                patch.object(MemoryStore, "send_message", readonly),
            ):
                result = ask(store, "claude", "ship the release")
            self.assertIn("SEEN_REQUEST True", result["reply"])
            self.assertFalse(result["recorded"])

    def test_a_sandbox_denial_explains_itself(self):
        from continuum.delegation import sandbox_hint

        denied = PermissionError("Access is denied")
        denied.winerror = 5
        self.assertIn("sandboxing Continuum", sandbox_hint(denied))
        self.assertIn("danger-full-access", sandbox_hint(denied))
        self.assertEqual(sandbox_hint(FileNotFoundError("no such file")), "")

    def test_the_mcp_reply_says_when_the_exchange_was_not_recorded(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "repo")
            store.initialize(100000, 0.8)
            store.write_handoff("renamed the payment client", "fix the failing retry test")

            def readonly(*_args, **_kwargs):
                raise sqlite3.OperationalError("attempt to write a readonly database")

            with (
                patch("continuum.cli.agent_command", side_effect=responder_command),
                patch.object(MemoryStore, "event", readonly),
                patch.object(MemoryStore, "send_message", readonly),
            ):
                text = call_tool(store, "ask_agent", {"agent": "claude", "request": "ship it"})["content"][0]["text"]
            self.assertIn("could not be written to shared memory", text)


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


class McpRegistrationTest(unittest.TestCase):
    def store(self, temporary: str) -> MemoryStore:
        store = MemoryStore(Path(temporary) / "repo")
        store.initialize(100000, 0.8)
        return store

    def parse_toml(self, path: Path):
        """Parse TOML where the runtime can, and check the escaping either way.

        tomllib arrived in 3.11 and Continuum supports 3.9, so on older runtimes
        this falls back to asserting the property the parser would have caught:
        a Windows path written with backslashes is not valid TOML, because `\\U`
        and friends are read as escape sequences.
        """
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("\\", text)
        try:
            import tomllib
        except ImportError:
            return None
        with path.open("rb") as handle:
            return tomllib.load(handle)

    def test_codex_config_is_valid_toml_and_idempotent(self):
        from continuum.cli import register_codex_mcp

        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            first = register_codex_mcp(store)
            path = store.project / ".codex" / "config.toml"
            parsed = self.parse_toml(path)
            if parsed is not None:
                self.assertEqual(parsed["mcp_servers"]["continuum"]["command"], "continuum")
            self.assertIn("registered in", first)
            self.assertIn("already registered", register_codex_mcp(store))

    def test_codex_config_keeps_existing_settings(self):
        from continuum.cli import register_codex_mcp

        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            path = store.project / ".codex" / "config.toml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('model = "gpt-5"\n', encoding="utf-8")
            register_codex_mcp(store)
            parsed = self.parse_toml(path)
            if parsed is not None:
                self.assertEqual(parsed["model"], "gpt-5")
                self.assertIn("continuum", parsed["mcp_servers"])
            else:
                text = path.read_text(encoding="utf-8")
                self.assertIn('model = "gpt-5"', text)
                self.assertIn("[mcp_servers.continuum]", text)

    def test_gemini_settings_are_merged_not_replaced(self):
        import json as json_module

        from continuum.cli import register_gemini_mcp

        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            path = store.project / ".gemini" / "settings.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json_module.dumps({"theme": "dark"}), encoding="utf-8")
            register_gemini_mcp(store)
            parsed = json_module.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(parsed["theme"], "dark")
            self.assertEqual(parsed["mcpServers"]["continuum"]["command"], "continuum")
            self.assertIn("already registered", register_gemini_mcp(store))

    def test_unreadable_gemini_settings_are_reported_not_overwritten(self):
        from continuum.cli import register_gemini_mcp

        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            path = store.project / ".gemini" / "settings.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not json", encoding="utf-8")
            self.assertIn("skipped", register_gemini_mcp(store))
            self.assertEqual(path.read_text(encoding="utf-8"), "{not json")


class FirstLaunchConnectsMcpTest(unittest.TestCase):
    """Launching an agent through Continuum wires it to the MCP server, so
    reaching other agents needs no separate setup command."""

    def test_first_launch_registers_and_later_launches_do_not(self):
        from continuum.cli import ensure_mcp_registered

        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "repo")
            store.initialize(100000, 0.8)
            first = ensure_mcp_registered(store, "codex")
            self.assertIsNotNone(first)
            self.assertIn("registered", str(first))
            self.assertEqual(store.read_config().get("mcp_connected"), ["codex"])
            self.assertIsNone(ensure_mcp_registered(store, "codex"))

    def test_an_unknown_agent_needs_no_registration(self):
        from continuum.cli import ensure_mcp_registered

        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "repo")
            store.initialize(100000, 0.8)
            self.assertIsNone(ensure_mcp_registered(store, "hermes"))

    def test_a_failed_registration_is_retried_next_time(self):
        from continuum import cli

        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "repo")
            store.initialize(100000, 0.8)
            with patch.dict(cli.MCP_REGISTRARS, {"codex": lambda _s: "Codex MCP registration skipped: boom"}):
                self.assertIn("skipped", str(cli.ensure_mcp_registered(store, "codex")))
            self.assertIsNone(store.read_config().get("mcp_connected"))
            self.assertIn("registered", str(cli.ensure_mcp_registered(store, "codex")))

    def test_go_connects_the_agent_it_launches(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with (
                patch("continuum.cli.agent_command", return_value=[sys.executable, "-c", "print('ok')"]),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["go", "codex", "--no-interactive", "--project", str(project)]), 0)
            self.assertIn("MCP server registered", output.getvalue())
            self.assertTrue((project / ".codex" / "config.toml").exists())


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
