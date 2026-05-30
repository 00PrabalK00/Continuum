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

    def test_classify_bug_fix(self):
        self.assertEqual(TeamManager.classify("fix the login crash"), "bug_fix")
        self.assertEqual(TeamManager.classify("resolve error in auth"), "bug_fix")
        self.assertEqual(TeamManager.classify("bug in payment flow"), "bug_fix")
        self.assertEqual(TeamManager.classify("failure on startup"), "bug_fix")

    def test_classify_large_refactor(self):
        self.assertEqual(TeamManager.classify("refactor the database layer"), "large_refactor")
        self.assertEqual(TeamManager.classify("rewrite the auth module"), "large_refactor")
        self.assertEqual(TeamManager.classify("migration to postgres"), "large_refactor")

    def test_classify_test_repair(self):
        self.assertEqual(TeamManager.classify("fix failing test for auth"), "test_repair")
        self.assertEqual(TeamManager.classify("repair broken assertion in login spec"), "test_repair")

    def test_classify_documentation(self):
        self.assertEqual(TeamManager.classify("update the README"), "documentation")
        self.assertEqual(TeamManager.classify("add documentation for API"), "documentation")

    def test_classify_new_feature(self):
        self.assertEqual(TeamManager.classify("add dark mode toggle"), "new_feature")
        self.assertEqual(TeamManager.classify("implement user preferences"), "new_feature")

    def test_team_list_returns_initiated_teams(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            manager = TeamManager(store)
            manager.init("default_dev_team")
            manager.init("fast_bugfix")
            teams = manager.list()
            self.assertIn("default_dev_team", teams)
            self.assertIn("fast_bugfix", teams)

    def test_team_list_returns_empty_when_no_teams(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            manager = TeamManager(store)
            self.assertEqual(manager.list(), [])

    def test_team_load_unknown_raises_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            manager = TeamManager(store)
            with self.assertRaisesRegex(TeamError, "not found"):
                manager.load("nonexistent")

    def test_explain_returns_steps_for_task_type(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            manager = TeamManager(store)
            manager.init("fast_bugfix")
            plan = manager.explain("fast_bugfix", "fix login crash")
            self.assertEqual(plan["task_type"], "bug_fix")
            self.assertGreater(len(plan["steps"]), 0)


if __name__ == "__main__":
    unittest.main()
