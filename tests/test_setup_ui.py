import tempfile
import unittest
from io import StringIO
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from continuum import setup_ui
from continuum.core import MemoryStore


def answers(*values):
    """A stand-in for input() that replies with each value in turn."""
    queue = list(values)
    return lambda _prompt="": queue.pop(0) if queue else ""


def fresh(temporary: str) -> MemoryStore:
    store = MemoryStore(Path(temporary) / "repo")
    store.initialize(100000, 0.8)
    return store


class PromptTest(unittest.TestCase):
    def test_pressing_enter_takes_the_default(self):
        with redirect_stdout(StringIO()):
            self.assertTrue(setup_ui.ask_yes_no("go?", True, answers("")))
            self.assertFalse(setup_ui.ask_yes_no("go?", False, answers("")))

    def test_it_asks_again_after_an_unrecognised_answer(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertTrue(setup_ui.ask_yes_no("go?", False, answers("maybe", "y")))
        self.assertIn("answer y or n", output.getvalue())

    def test_a_cancelled_prompt_declines(self):
        def interrupt(_prompt=""):
            raise KeyboardInterrupt

        with redirect_stdout(StringIO()):
            self.assertFalse(setup_ui.ask_yes_no("go?", True, interrupt))

    def test_choosing_a_number_returns_its_key(self):
        options = [("none", "No"), ("ollama", "Local model")]
        with redirect_stdout(StringIO()):
            self.assertEqual(setup_ui.choose("pick", options, answers("2")), "ollama")
            self.assertEqual(setup_ui.choose("pick", options, answers("")), "none")
            self.assertEqual(setup_ui.choose("pick", options, answers("9", "1")), "none")


class InteractiveDetectionTest(unittest.TestCase):
    def test_a_pipe_is_not_a_terminal(self):
        self.assertFalse(setup_ui.interactive(StringIO()))


class SemanticSetupTest(unittest.TestCase):
    def test_it_offers_to_install_ollama_when_absent(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(StringIO()) as output:
            with (
                patch.object(setup_ui.shutil, "which", return_value=None),
                patch.object(setup_ui, "install_command", return_value=(["winget", "install"], "winget install")),
            ):
                report = setup_ui.enable_semantic_search(fresh(temporary), answers("n"))
            self.assertIn("Ollama not installed", report)
            self.assertIn("winget install", output.getvalue())

    def test_it_installs_nothing_unless_asked(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(StringIO()):
            with (
                patch.object(setup_ui.shutil, "which", return_value=None),
                patch.object(setup_ui, "install_command", return_value=(["winget", "install"], "winget install")),
                patch.object(setup_ui.subprocess, "run") as ran,
            ):
                setup_ui.enable_semantic_search(fresh(temporary), answers("n"))
            ran.assert_not_called()

    def test_agreeing_runs_the_command_that_was_shown(self):
        shown = ["winget", "install", "--id", "Ollama.Ollama"]
        with redirect_stdout(StringIO()) as output:
            with (
                patch.object(setup_ui, "install_command", return_value=(shown, "winget install --id Ollama.Ollama")),
                patch.object(setup_ui.subprocess, "run", return_value=type("R", (), {"returncode": 0})()) as ran,
                patch.object(setup_ui.shutil, "which", return_value="/usr/bin/ollama"),
            ):
                self.assertTrue(setup_ui.offer_install(answers("y")))
            self.assertEqual(list(ran.call_args[0][0]), shown)
        self.assertIn("winget install --id Ollama.Ollama", output.getvalue())

    def test_the_default_answer_is_no(self):
        with redirect_stdout(StringIO()):
            with (
                patch.object(setup_ui, "install_command", return_value=(["x"], "x")),
                patch.object(setup_ui.subprocess, "run") as ran,
            ):
                self.assertFalse(setup_ui.offer_install(answers("")))
            ran.assert_not_called()

    def test_a_failed_install_is_reported_not_raised(self):
        with redirect_stdout(StringIO()) as output:
            with (
                patch.object(setup_ui, "install_command", return_value=(["x"], "x")),
                patch.object(setup_ui.subprocess, "run", return_value=type("R", (), {"returncode": 1})()),
            ):
                self.assertFalse(setup_ui.offer_install(answers("y")))
        self.assertIn("did not finish", output.getvalue())

    def test_with_no_package_manager_it_points_at_the_download(self):
        with redirect_stdout(StringIO()) as output:
            with patch.object(setup_ui, "install_command", return_value=None):
                self.assertFalse(setup_ui.offer_install(answers("y")))
        self.assertIn("ollama.com/download", output.getvalue())

    def test_the_install_command_matches_the_platform(self):
        with patch.object(setup_ui.sys, "platform", "win32"):
            with patch.object(setup_ui.shutil, "which", return_value="/winget"):
                self.assertIn("winget", setup_ui.install_command()[1])
            with patch.object(setup_ui.shutil, "which", return_value=None):
                self.assertIsNone(setup_ui.install_command())
        with patch.object(setup_ui.sys, "platform", "darwin"):
            with patch.object(setup_ui.shutil, "which", return_value="/brew"):
                self.assertIn("brew", setup_ui.install_command()[1])

    def test_declining_the_download_leaves_search_on_words(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(StringIO()):
            with (
                patch.object(setup_ui.shutil, "which", return_value="/usr/bin/ollama"),
                patch.object(setup_ui, "ollama_running", return_value=True),
                patch.object(setup_ui, "ollama_models", return_value=["qwen2.5:7b"]),
                patch.object(setup_ui, "pull_model") as pull,
            ):
                report = setup_ui.enable_semantic_search(fresh(temporary), answers("n"))
            pull.assert_not_called()
            self.assertIn("wording only", report)

    def test_a_failed_download_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(StringIO()):
            with (
                patch.object(setup_ui.shutil, "which", return_value="/usr/bin/ollama"),
                patch.object(setup_ui, "ollama_running", return_value=True),
                patch.object(setup_ui, "ollama_models", return_value=[]),
                patch.object(setup_ui, "pull_model", return_value=(False, "no disk space")),
            ):
                report = setup_ui.enable_semantic_search(fresh(temporary), answers("y"))
            self.assertIn("no disk space", report)
            self.assertIn("wording only", report)

    def test_a_stopped_ollama_is_started(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(StringIO()):
            with (
                patch.object(setup_ui.shutil, "which", return_value="/usr/bin/ollama"),
                patch.object(setup_ui, "ollama_running", return_value=False),
                patch.object(setup_ui, "start_ollama", return_value=True) as started,
                patch.object(setup_ui, "ollama_models", return_value=["nomic-embed-text:latest"]),
            ):
                setup_ui.enable_semantic_search(fresh(temporary))
            started.assert_called_once()

    def test_it_gives_up_gracefully_when_ollama_will_not_start(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(StringIO()):
            with (
                patch.object(setup_ui.shutil, "which", return_value="/usr/bin/ollama"),
                patch.object(setup_ui, "ollama_running", return_value=False),
                patch.object(setup_ui, "start_ollama", return_value=False),
            ):
                report = setup_ui.enable_semantic_search(fresh(temporary))
            self.assertIn("could not start Ollama", report)


class HandoffModelSetupTest(unittest.TestCase):
    def test_with_nothing_available_it_keeps_recorded_state(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(StringIO()):
            with (
                patch.object(setup_ui.shutil, "which", return_value=None),
                patch.dict("os.environ", {}, clear=True),
            ):
                report = setup_ui.configure_handoff_model(fresh(temporary))
            self.assertIn("recorded state", report)

    def test_declining_keeps_recorded_state(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(StringIO()):
            with patch.object(setup_ui, "installed_chat_model", return_value="qwen2.5:7b"):
                report = setup_ui.configure_handoff_model(fresh(temporary), answers("1"))
            self.assertIn("recorded state", report)

    def test_choosing_ollama_records_the_choice(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(StringIO()):
            store = fresh(temporary)
            with patch.object(setup_ui, "installed_chat_model", return_value="qwen2.5:7b"):
                report = setup_ui.configure_handoff_model(store, answers("2"))
            self.assertIn("ollama", report)
            from continuum.handoff_llm import read_handoff_model

            self.assertEqual((read_handoff_model(store) or {}).get("provider"), "ollama")


class ChatModelTest(unittest.TestCase):
    """A fresh Ollama installed for search may only hold the embedding model.
    Offering summaries on the strength of Ollama running produces a config where
    every summary fails and silently falls back, while setup reports success."""

    def test_an_embedding_only_install_offers_no_summary_model(self):
        with (
            patch.object(setup_ui.shutil, "which", return_value="/usr/bin/ollama"),
            patch.object(setup_ui, "ollama_running", return_value=True),
            patch.object(setup_ui, "ollama_models", return_value=["nomic-embed-text:latest"]),
        ):
            self.assertIsNone(setup_ui.installed_chat_model())

    def test_a_chat_model_is_found_when_present(self):
        with (
            patch.object(setup_ui.shutil, "which", return_value="/usr/bin/ollama"),
            patch.object(setup_ui, "ollama_running", return_value=True),
            patch.object(setup_ui, "ollama_models", return_value=["nomic-embed-text:latest", "qwen2.5:7b"]),
        ):
            self.assertEqual(setup_ui.installed_chat_model(), "qwen2.5:7b")

    def test_summaries_are_not_offered_without_a_chat_model(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(StringIO()) as output:
            with (
                patch.object(setup_ui, "installed_chat_model", return_value=None),
                patch.dict("os.environ", {}, clear=True),
            ):
                report = setup_ui.configure_handoff_model(fresh(temporary))
            self.assertIn("recorded state", report)
            self.assertNotIn("Ollama", output.getvalue())

    def test_the_found_model_is_recorded_not_the_provider_default(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(StringIO()):
            store = fresh(temporary)
            with patch.object(setup_ui, "installed_chat_model", return_value="qwen2.5:7b"):
                report = setup_ui.configure_handoff_model(store, answers("2"))
            from continuum.handoff_llm import read_handoff_model

            self.assertEqual(read_handoff_model(store)["model"], "qwen2.5:7b")
            self.assertIn("qwen2.5:7b", report)


class IndexingTest(unittest.TestCase):
    def test_an_empty_project_says_so(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIn("no recorded memory", setup_ui.index_existing_memory(fresh(temporary)))

    def test_indexing_stops_cleanly_when_the_model_dies(self):
        from continuum.providers import ProviderError

        with tempfile.TemporaryDirectory() as temporary:
            store = fresh(temporary)
            store.event("handoff", {"task": "chose PostgreSQL", "next_step": "schema"})
            with patch("continuum.providers.ProviderManager.embed", side_effect=ProviderError("down")):
                report = setup_ui.index_existing_memory(store)
            self.assertIn("stopped responding", report)


class InstallCommandTest(unittest.TestCase):
    def test_a_non_interactive_run_asks_nothing(self):
        from continuum.cli import main

        with tempfile.TemporaryDirectory() as temporary:
            output = StringIO()
            with (
                patch("continuum.cli.setup_is_interactive", return_value=False),
                patch("continuum.cli.ask_setup_yes_no") as asked,
                redirect_stdout(output),
            ):
                self.assertEqual(main(["install", "--project", str(Path(temporary) / "repo")]), 0)
            asked.assert_not_called()
            self.assertIn("Nothing else to run", output.getvalue())

    def test_yes_connects_agents_without_questions(self):
        from continuum.cli import main

        with tempfile.TemporaryDirectory() as temporary:
            output = StringIO()
            with (
                patch("continuum.cli.setup_is_interactive", return_value=True),
                patch("continuum.cli.ask_setup_yes_no") as asked,
                redirect_stdout(output),
            ):
                self.assertEqual(main(["install", "--yes", "--project", str(Path(temporary) / "repo")]), 0)
            asked.assert_not_called()

    def test_the_guided_run_offers_search_and_summaries(self):
        from continuum.cli import main

        with tempfile.TemporaryDirectory() as temporary:
            output = StringIO()
            with (
                patch("continuum.cli.setup_is_interactive", return_value=True),
                patch("continuum.cli.ask_setup_yes_no", return_value=False) as asked,
                patch("continuum.cli.configure_handoff_model", return_value="handoff summaries use recorded state"),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["install", "--project", str(Path(temporary) / "repo")]), 0)
            asked.assert_called_once()
            text = output.getvalue()
            self.assertIn("Search", text)
            self.assertIn("Summaries", text)


if __name__ == "__main__":
    unittest.main()
