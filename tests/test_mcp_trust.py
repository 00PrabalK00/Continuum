import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from continuum import mcp_trust
from continuum.cli import main
from continuum.core import MemoryStore


class TrustRegistryUnitTest(unittest.TestCase):
    def test_unknown_server_default_untrusted(self):
        registry = mcp_trust._empty_registry()
        self.assertFalse(mcp_trust.is_tool_allowed(registry, "ghost", "read_file"))

    def test_trusted_status_allows_tools_by_default(self):
        registry = mcp_trust._empty_registry()
        mcp_trust.add_server(registry, "acme", "trusted")
        self.assertTrue(mcp_trust.is_tool_allowed(registry, "acme", "read_file"))

    def test_untrusted_and_blocked_deny_by_default(self):
        registry = mcp_trust._empty_registry()
        mcp_trust.add_server(registry, "u", "untrusted")
        mcp_trust.add_server(registry, "b", "blocked")
        self.assertFalse(mcp_trust.is_tool_allowed(registry, "u", "any"))
        self.assertFalse(mcp_trust.is_tool_allowed(registry, "b", "any"))

    def test_allow_list_overrides_untrusted(self):
        registry = mcp_trust._empty_registry()
        mcp_trust.add_server(registry, "u", "untrusted")
        mcp_trust.allow_tool(registry, "u", "read_file")
        self.assertTrue(mcp_trust.is_tool_allowed(registry, "u", "read_file"))
        self.assertFalse(mcp_trust.is_tool_allowed(registry, "u", "write_file"))

    def test_deny_overrides_allow(self):
        registry = mcp_trust._empty_registry()
        mcp_trust.add_server(registry, "acme", "trusted")
        mcp_trust.allow_tool(registry, "acme", "tool")
        mcp_trust.deny_tool(registry, "acme", "tool")
        self.assertFalse(mcp_trust.is_tool_allowed(registry, "acme", "tool"))
        # allow moves it back off the deny list.
        mcp_trust.allow_tool(registry, "acme", "tool")
        self.assertTrue(mcp_trust.is_tool_allowed(registry, "acme", "tool"))

    def test_save_load_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / ".continuum"
            state.mkdir()
            registry = mcp_trust._empty_registry()
            mcp_trust.add_server(registry, "acme", "trusted")
            mcp_trust.save_registry(state, registry)
            loaded = mcp_trust.load_registry(state)
            self.assertEqual(loaded["servers"][0]["server"], "acme")
            self.assertEqual(loaded["servers"][0]["status"], "trusted")

    def test_add_duplicate_rejected(self):
        registry = mcp_trust._empty_registry()
        mcp_trust.add_server(registry, "acme")
        with self.assertRaises(mcp_trust.TrustError):
            mcp_trust.add_server(registry, "acme")


class TrustCliTest(unittest.TestCase):
    def _store(self, temporary):
        project = Path(temporary) / "repo"
        store = MemoryStore(project)
        store.initialize(1000, 0.8)
        return store, project

    def _run(self, argv):
        output = StringIO()
        with redirect_stdout(output):
            rc = main(argv)
        return rc, output.getvalue()

    def test_add_set_allow_deny_remove_with_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            store, project = self._store(temporary)
            base = ["--project", str(project)]
            self.assertEqual(self._run(["mcp", "trust", "add", "acme", *base])[0], 0)
            self.assertEqual(self._run(["mcp", "trust", "set", "acme", "--status", "trusted", *base])[0], 0)
            self.assertEqual(self._run(["mcp", "trust", "deny", "acme", "danger", *base])[0], 0)
            self.assertEqual(self._run(["mcp", "trust", "allow", "acme", "read", *base])[0], 0)
            rc, listed = self._run(["mcp", "trust", "list", *base])
            self.assertEqual(rc, 0)
            self.assertIn("acme", listed)
            self.assertIn("trusted", listed)
            # Gating reflects the persisted registry.
            registry = mcp_trust.load_registry(store.state_dir)
            self.assertTrue(mcp_trust.is_tool_allowed(registry, "acme", "read"))
            self.assertFalse(mcp_trust.is_tool_allowed(registry, "acme", "danger"))
            # Each mutation recorded an audit event.
            events = [e for e in store.recent_events(50) if e["kind"] == "mcp_trust_changed"]
            actions = {e["payload"]["action"] for e in events}
            self.assertEqual(actions, {"add", "set_status", "deny_tool", "allow_tool"})
            # Remove also audits.
            self.assertEqual(self._run(["mcp", "trust", "remove", "acme", *base])[0], 0)
            events = [e for e in store.recent_events(50) if e["kind"] == "mcp_trust_changed"]
            self.assertIn("remove", {e["payload"]["action"] for e in events})
            self.assertEqual(mcp_trust.load_registry(store.state_dir)["servers"], [])


if __name__ == "__main__":
    unittest.main()
