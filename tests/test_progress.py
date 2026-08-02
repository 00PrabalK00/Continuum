import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from continuum.cli import finalize_handoff, main
from continuum.core import MemoryStore
from continuum.mcp_server import call_tool, tool_definitions
from continuum.progress import base_next_step, record_progress


def fresh(temporary: str) -> MemoryStore:
    store = MemoryStore(Path(temporary) / "repo")
    store.initialize(100000, 0.8)
    return store


def started(store: MemoryStore) -> None:
    store.event("handoff", {"task": "add retries", "next_step": "write the retry test"})
    store.write_handoff("add retries", "write the retry test")


class AnnotationStackingTest(unittest.TestCase):
    """A carried-forward next step is marked unconfirmed when a session changed
    files. Rebuilding from the annotated text instead of the original made the
    mark compound on every cycle."""

    def cycle(self, store, label):
        with patch.object(MemoryStore, "git_or_watch_changes", lambda _s: ["M retry.py"]):
            with redirect_stdout(StringIO()):
                finalize_handoff(store, "claude", label, ["out"], 0, False)

    def test_a_save_between_sessions_does_not_compound_the_annotation(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            started(store)
            for index in range(3):
                self.cycle(store, f"S{index}")
                record_progress(store)
            step = store.latest_task()[1]
            self.assertEqual(step.count("Check whether that finished"), 1)
            self.assertIn("write the retry test", step)

    def test_the_cli_save_path_carries_the_base(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            started(store)
            self.cycle(store, "S1")
            with redirect_stdout(StringIO()):
                main(["save", "--project", str(store.project)])
            recorded = [item for item in store.recent_handoffs(5)][0]["payload"]
            self.assertEqual(recorded.get("base_next_step"), "write the retry test")

    def test_the_mcp_save_path_carries_the_base(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            started(store)
            self.cycle(store, "S1")
            call_tool(store, "save_progress", {"task": "still on retries"})
            recorded = store.recent_handoffs(5)[0]["payload"]
            self.assertEqual(recorded.get("base_next_step"), "write the retry test")

    def test_write_handoff_carries_the_base_too(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            started(store)
            call_tool(store, "write_handoff", {"task": "t", "next_step": "n"})
            self.assertIn("base_next_step", store.recent_handoffs(5)[0]["payload"])

    def test_the_base_is_the_original_not_the_annotation(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            started(store)
            self.cycle(store, "S1")
            self.assertEqual(base_next_step(store), "write the retry test")


class SaveProgressToolTest(unittest.TestCase):
    def test_the_tool_is_advertised_with_its_trigger(self):
        tool = next(item for item in tool_definitions() if item["name"] == "save_progress")
        description = tool["description"]
        self.assertIn("running low on context", description)
        self.assertIn("user asks", description)
        self.assertEqual(tool["inputSchema"].get("required", []), [])

    def test_an_agent_can_supply_both_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            text = call_tool(store, "save_progress", {"task": "wired the webhook", "next_step": "add a test"})
            self.assertIn("wired the webhook", text["content"][0]["text"])
            self.assertEqual(store.latest_task(), ("wired the webhook", "add a test"))

    def test_an_agent_can_supply_nothing_and_continuum_fills_it_in(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            started(store)
            call_tool(store, "save_progress", {})
            self.assertEqual(store.latest_task()[0], "add retries")

    def test_an_empty_project_says_what_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            with self.assertRaises(ValueError) as caught:
                call_tool(store, "save_progress", {})
            self.assertIn("Nothing to record yet", str(caught.exception))

    def test_the_handoff_model_writes_the_summary_when_configured(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            started(store)
            with (
                patch("continuum.progress.generate_handoff", return_value=("model task", "model next")),
                patch("continuum.progress.read_handoff_model", return_value={"provider": "ollama", "model": "qwen"}),
            ):
                result = record_progress(store)
            self.assertEqual(result["task"], "model task")
            self.assertIn("ollama:qwen", result["message"])

    def test_a_failing_handoff_model_falls_back_to_what_was_recorded(self):
        from continuum.providers import ProviderError

        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            started(store)
            with patch("continuum.progress.generate_handoff", side_effect=ProviderError("down")):
                result = record_progress(store)
            self.assertEqual(result["task"], "add retries")


class InstructionTest(unittest.TestCase):
    def test_agents_are_told_when_to_record(self):
        from continuum.integrations import AGENT_INSTRUCTIONS

        self.assertIn("save_progress", AGENT_INSTRUCTIONS)
        self.assertIn("running low on context", AGENT_INSTRUCTIONS)
        self.assertIn("whenever the user asks", AGENT_INSTRUCTIONS)
        # Continuum cannot see the agent's context, so the instruction must say
        # the judgement belongs to the agent.
        self.assertIn("only one who can see", AGENT_INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
