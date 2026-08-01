import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from continuum.cli import main
from continuum.core import MemoryStore
from continuum.handoff_llm import (
    generate_handoff,
    parse_reply,
    read_handoff_model,
    write_handoff_model,
)
from continuum.providers import ProviderError


class ParseReplyTest(unittest.TestCase):
    def test_parses_plain_task_and_next_lines(self):
        parsed = parse_reply("TASK: Fix the auth bug.\nNEXT: Run the failing login test.")
        self.assertEqual(parsed, ("Fix the auth bug.", "Run the failing login test."))

    def test_parses_decorated_and_lowercase_lines(self):
        parsed = parse_reply("Some preamble.\n- **task:** refactor parser\n* next: add a regression test\n")
        self.assertEqual(parsed, ("refactor parser", "add a regression test"))

    def test_returns_none_when_lines_missing(self):
        self.assertIsNone(parse_reply("The session went well overall."))
        self.assertIsNone(parse_reply("TASK: only a task line"))


class HandoffModelConfigTest(unittest.TestCase):
    def test_roundtrip_set_and_clear(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "repo")
            store.initialize(200_000, 0.8)
            self.assertIsNone(read_handoff_model(store))
            write_handoff_model(store, "ollama", "llama3.1:8b")
            self.assertEqual(read_handoff_model(store), {"provider": "ollama", "model": "llama3.1:8b"})
            write_handoff_model(store, None)
            self.assertIsNone(read_handoff_model(store))

    def test_generate_returns_none_when_unconfigured(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "repo")
            store.initialize(200_000, 0.8)
            self.assertIsNone(generate_handoff(store))


class HandoffLlmCliTest(unittest.TestCase):
    def test_set_enables_provider_and_show_reports_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["handoff-llm", "set", "--project", str(project), "ollama", "llama3.1:8b"]), 0)
                self.assertEqual(main(["handoff-llm", "show", "--project", str(project)]), 0)
            text = output.getvalue()
            self.assertIn("Enabled provider: ollama", text)
            self.assertIn("Handoff LLM set: ollama (llama3.1:8b)", text)
            self.assertIn("Provider: ollama", text)
            self.assertIn("Model: llama3.1:8b", text)

    def test_set_rejects_agent_cli_providers(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            with redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(["handoff-llm", "set", "--project", str(project), "claude_code"])
            self.assertIn("model provider", str(raised.exception))

    def test_off_clears_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["handoff-llm", "set", "--project", str(project), "ollama"]), 0)
                self.assertEqual(main(["handoff-llm", "off", "--project", str(project)]), 0)
                self.assertEqual(main(["handoff-llm", "show", "--project", str(project)]), 0)
            self.assertIn("No handoff LLM configured", output.getvalue())

    def test_bare_save_uses_handoff_llm_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                self.assertEqual(main(["save", "--project", str(project), "seed work | seed next"]), 0)
                self.assertEqual(main(["handoff-llm", "set", "--project", str(project), "ollama"]), 0)
                with patch(
                    "continuum.providers.ProviderManager.ask",
                    return_value="TASK: Wired the retry logic.\nNEXT: Run the integration suite.",
                ):
                    self.assertEqual(main(["save", "--project", str(project)]), 0)
            text = output.getvalue()
            self.assertIn("Saved: Wired the retry logic.", text)
            self.assertIn("Next:  Run the integration suite.", text)
            self.assertIn("Summarized by handoff LLM: ollama", text)
            handoff = (project / ".continuum" / "latest_handoff.md").read_text(encoding="utf-8")
            self.assertIn("Wired the retry logic.", handoff)

    def test_bare_save_falls_back_when_provider_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                self.assertEqual(main(["save", "--project", str(project), "seed work | seed next"]), 0)
                self.assertEqual(main(["handoff-llm", "set", "--project", str(project), "ollama"]), 0)
                with patch(
                    "continuum.providers.ProviderManager.ask",
                    side_effect=ProviderError("connection refused"),
                ):
                    self.assertEqual(main(["save", "--project", str(project)]), 0)
            text = output.getvalue()
            self.assertIn("Handoff LLM unavailable", text)
            self.assertIn("Saved: seed work", text)


if __name__ == "__main__":
    unittest.main()
