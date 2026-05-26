import tempfile
import unittest
import json
from pathlib import Path

from continuum.core import MemoryStore
from continuum.teams import TeamError, TeamManager


class TeamManagerTest(unittest.TestCase):
    def test_default_team_routes_bug_fix_into_scoped_tasks(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            manager = TeamManager(store)
            manager.init("default_dev_team")

            plan = manager.explain("default_dev_team", "fix auth crash")
            tasks = manager.plan_tasks("default_dev_team", "fix auth crash")

            self.assertEqual(plan["task_type"], "bug_fix")
            self.assertEqual(plan["steps"][0]["provider"], "gemini_cli")
            self.assertEqual(tasks[2]["agent"], "coder:claude_code")

    def test_model_provider_cannot_be_configured_as_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            manager = TeamManager(store)

            with self.assertRaises(TeamError):
                manager.validate(
                    {
                        "agents": {"bad": {"provider": "ollama", "can_edit_files": True}},
                        "routing": {"bug_fix": ["bad"]},
                    }
                )

    def test_unknown_preset_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            with self.assertRaisesRegex(TeamError, "Unknown team preset"):
                TeamManager(store).init("not_a_team")

    def test_invalid_sub_agent_delegation_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = TeamManager(MemoryStore(Path(temporary) / "project"))
            with self.assertRaisesRegex(TeamError, "Invalid delegates"):
                manager.validate(
                    {
                        "agents": {
                            "lead": {"provider": "codex", "delegates": ["missing"]},
                        },
                        "routing": {"bug_fix": ["lead"]},
                    }
                )

    def test_configured_custom_model_provider_cannot_be_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            (store.state_dir / "providers.json").write_text(
                json.dumps({"providers": {"gateway": {"kind": "model"}}}), encoding="utf-8"
            )
            manager = TeamManager(store)

            with self.assertRaisesRegex(TeamError, "Model provider"):
                manager.validate(
                    {
                        "agents": {"writer": {"provider": "gateway", "can_edit_files": True}},
                        "routing": {"bug_fix": ["writer"]},
                    }
                )


if __name__ == "__main__":
    unittest.main()
