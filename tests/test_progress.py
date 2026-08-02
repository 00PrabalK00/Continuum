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


class BaseSelectionTest(unittest.TestCase):
    """Which next step becomes the base decides what a later session tells the
    agent to continue, so getting it wrong resurrects abandoned work."""

    def test_the_newest_base_is_used_not_the_oldest(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            for index in (1, 2, 3):
                store.event("handoff", {"task": f"task {index}", "next_step": f"step {index}"})
            self.assertEqual(base_next_step(store), "step 3")

    def test_an_explicit_next_step_becomes_the_base(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            started(store)
            record_progress(store, "moved on", "deploy the fix")
            payload = store.recent_handoffs(1)[0]["payload"]
            self.assertEqual(payload["next_step"], "deploy the fix")
            self.assertEqual(payload["base_next_step"], "deploy the fix")

    def test_a_carried_step_keeps_the_original_base(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            started(store)
            with patch.object(MemoryStore, "git_or_watch_changes", lambda _s: ["M retry.py"]):
                with redirect_stdout(StringIO()):
                    finalize_handoff(store, "claude", "S1", ["out"], 0, False)
            record_progress(store)
            payload = store.recent_handoffs(1)[0]["payload"]
            self.assertEqual(payload["base_next_step"], "write the retry test")

    def test_a_task_only_save_still_records_a_next_step(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            started(store)
            record_progress(store, "still on retries")
            payload = store.recent_handoffs(1)[0]["payload"]
            self.assertTrue(payload["next_step"], "a task-only save must not record next_step as None")
            self.assertEqual(payload["next_step"], "write the retry test")

    def test_a_task_only_save_uses_the_model_when_configured(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            started(store)
            with (
                patch("continuum.progress.generate_handoff", return_value=("ignored", "model next")),
                patch("continuum.progress.read_handoff_model", return_value={"provider": "ollama", "model": "q"}),
            ):
                record_progress(store, "supplied task")
            payload = store.recent_handoffs(1)[0]["payload"]
            self.assertEqual(payload["task"], "supplied task")
            self.assertEqual(payload["next_step"], "model next")
            self.assertEqual(payload["base_next_step"], "model next")

    def test_a_task_only_save_works_on_an_empty_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            record_progress(store, "first thing recorded")
            self.assertEqual(store.latest_task()[0], "first thing recorded")


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


class SaveSyntaxTest(unittest.TestCase):
    """The README documents `save "task | next: thing"`. Keeping the label made
    every later display read "Next: next: thing"."""

    def saved(self, text):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            out = StringIO()
            with redirect_stdout(out):
                main(["save", "--project", str(store.project), text])
            return store.latest_task(), out.getvalue()

    def test_the_documented_next_label_is_not_repeated_back(self):
        (task, step), printed = self.saved("fixed the auth bug | next: test the retry logic")
        self.assertEqual(task, "fixed the auth bug")
        self.assertEqual(step, "test the retry logic")
        self.assertNotIn("next: next:", printed.lower())

    def test_the_label_is_optional(self):
        self.assertEqual(self.saved("fixed it | test the retry logic")[0][1],
                         "test the retry logic")

    def test_the_label_is_matched_whatever_its_case(self):
        self.assertEqual(self.saved("fixed it | Next:  test the retry logic")[0][1],
                         "test the retry logic")

    def test_the_word_next_inside_a_step_is_left_alone(self):
        self.assertEqual(self.saved("fixed it | next release needs a changelog")[0][1],
                         "next release needs a changelog")


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
