import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from continuum.core import MemoryStore
from continuum.providers import ProviderError, ProviderManager


class ProviderManagerTest(unittest.TestCase):
    def test_default_provider_config_distinguishes_agents_and_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = ProviderManager(Path(temporary))
            config = manager.read()

            self.assertEqual(config["providers"]["ollama"]["kind"], "model")
            self.assertEqual(config["providers"]["codex"]["kind"], "agent")
            self.assertFalse(config["providers"]["ollama"]["enabled"])
            self.assertFalse(config["providers"]["codex"]["enabled"])

    def test_ollama_chat_and_embedding_use_openai_compatible_shape(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = ProviderManager(Path(temporary))
            manager.add("ollama")
            with patch.object(
                manager,
                "_json_request",
                side_effect=[
                    {"choices": [{"message": {"content": "short summary"}}]},
                    {"data": [{"embedding": [0.1, 0.2, 0.3]}]},
                ],
            ):
                answer = manager.ask("ollama", "summarize work")
                model, vector = manager.embed("ollama", "memory")

            self.assertEqual(answer, "short summary")
            self.assertEqual(model, "nomic-embed-text")
            self.assertEqual(len(vector), 3)

    def test_openrouter_without_key_has_specific_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = ProviderManager(Path(temporary))
            manager.add("openrouter")
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaisesRegex(ProviderError, "OPENROUTER_API_KEY"):
                    manager.test("openrouter")

    def test_embedding_is_persisted_as_structured_memory(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)

            store.store_embedding("M:current", "ollama", "embed-model", [0.1, 0.2], "current note")

            events = store.search("memory_embedded")
            self.assertEqual(events[0]["payload"]["dimensions"], 2)

    def test_enabled_agent_receives_bounded_prompt_through_adapter(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = ProviderManager(Path(temporary))
            manager.add("codex")
            completed = Mock(returncode=0, stdout="review complete", stderr="")
            with patch("continuum.providers.shutil.which", return_value="codex"), patch(
                "continuum.providers.subprocess.run", return_value=completed
            ) as invoked:
                result = manager.run_agent("codex", "inspect current state", Path(temporary))

            self.assertEqual(result, "review complete")
            self.assertIn("exec", invoked.call_args.args[0])
            self.assertIn("inspect current state", invoked.call_args.args[0])

    def test_agent_launch_os_error_has_actionable_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = ProviderManager(Path(temporary))
            manager.add("codex")
            with patch("continuum.providers.shutil.which", return_value="codex"), patch(
                "continuum.providers.subprocess.run", side_effect=OSError("gone")
            ):
                with self.assertRaisesRegex(ProviderError, "continuum providers test codex"):
                    manager.run_agent("codex", "inspect", Path(temporary))


if __name__ == "__main__":
    unittest.main()
