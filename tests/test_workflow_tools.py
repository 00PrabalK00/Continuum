import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from continuum.core import MemoryStore


class WorkflowToolTest(unittest.TestCase):
    """Agents can plan and run multi-agent workflows, but only inside the same
    write permissions the CLI enforces."""

    def project(self, temporary: str) -> MemoryStore:
        store = MemoryStore(Path(temporary) / "repo")
        store.initialize(100000, 0.8)
        from continuum.teams import TeamManager

        TeamManager(store).init("local_agent_team")
        return store

    def call(self, store, name, args):
        from continuum.mcp_server import call_tool

        return call_tool(store, name, args)["content"][0]["text"]

    def test_the_tools_are_advertised(self):
        from continuum.mcp_server import tool_definitions

        names = [item["name"] for item in tool_definitions()]
        for expected in ("list_teams", "plan_workflow", "run_workflow", "get_workflow"):
            self.assertIn(expected, names)

    def test_teams_list_their_roles_and_providers(self):
        with tempfile.TemporaryDirectory() as temporary:
            text = self.call(self.project(temporary), "list_teams", {})
            self.assertIn("local_agent_team", text)
            self.assertIn("codex", text)

    def test_planning_runs_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.project(temporary)
            text = self.call(store, "plan_workflow", {"team": "local_agent_team", "request": "fix the retry test"})
            self.assertIn("Nothing has run yet", text)
            self.assertIn("W0001", text)

    def test_running_without_a_write_list_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.project(temporary)
            with self.assertRaises(ValueError) as caught:
                self.call(store, "run_workflow", {"team": "local_agent_team", "request": "fix it"})
            self.assertIn("allow_files is required", str(caught.exception))

    def test_the_write_list_is_passed_through_unchanged(self):
        captured = {}

        def fake_run(self, workflow, team, request, allowed, mode):
            captured["allowed"] = allowed
            captured["ran"] = workflow["workflow_id"]
            return {**workflow, "status": "DONE"}

        with tempfile.TemporaryDirectory() as temporary:
            store = self.project(temporary)
            self.call(store, "plan_workflow", {"team": "local_agent_team", "request": "fix it"})
            with patch("continuum.orchestration.Orchestrator._run_workflow", fake_run):
                self.call(store, "run_workflow", {"workflow_id": "W0001", "allow_files": ["src/a.py"]})
            self.assertEqual(captured["allowed"], ["src/a.py"])

    def test_running_executes_the_workflow_that_was_planned(self):
        """`execute` plans a second workflow, abandoning the one the caller was
        shown. The plan-then-run flow has to run the plan it returned."""
        captured = {}

        def fake_run(self, workflow, team, request, allowed, mode):
            captured["ran"] = workflow["workflow_id"]
            return {**workflow, "status": "DONE"}

        with tempfile.TemporaryDirectory() as temporary:
            store = self.project(temporary)
            self.call(store, "plan_workflow", {"team": "local_agent_team", "request": "fix the retry test"})
            with patch("continuum.orchestration.Orchestrator._run_workflow", fake_run):
                self.call(store, "run_workflow", {"workflow_id": "W0001", "allow_files": []})
            self.assertEqual(captured["ran"], "W0001")
            self.assertEqual([w["workflow_id"] for w in store.list_workflows(10)], ["W0001"])

    def test_running_without_an_id_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.project(temporary)
            with self.assertRaises(ValueError) as caught:
                self.call(store, "run_workflow", {"allow_files": []})
            self.assertIn("workflow_id is required", str(caught.exception))

    def test_an_unknown_workflow_id_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.project(temporary)
            with self.assertRaises(ValueError):
                self.call(store, "run_workflow", {"workflow_id": "W9999", "allow_files": []})

    def test_an_unknown_team_is_a_tool_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.project(temporary)
            with self.assertRaises(ValueError):
                self.call(store, "plan_workflow", {"team": "nope", "request": "fix it"})

    def test_a_planned_workflow_can_be_read_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.project(temporary)
            self.call(store, "plan_workflow", {"team": "local_agent_team", "request": "fix the retry test"})
            text = self.call(store, "get_workflow", {"workflow_id": "W0001"})
            self.assertIn("local_agent_team", text)
            self.assertIn("fix the retry test", text)

    def test_an_unknown_workflow_says_so(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.project(temporary)
            self.assertIn("not found", self.call(store, "get_workflow", {"workflow_id": "W9999"}))


if __name__ == "__main__":
    unittest.main()
