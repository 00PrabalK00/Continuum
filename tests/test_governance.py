import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from continuum import audit_export
from continuum.cli import main
from continuum.core import MemoryStore
from continuum.providers import ProviderError, ProviderManager


class AuditExportTest(unittest.TestCase):
    def _store(self, temporary):
        store = MemoryStore(Path(temporary) / "repo")
        store.initialize(1000, 0.8)
        store.event("decision", {"summary": "chose sqlite"})
        return store

    def test_json_shape(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            text = audit_export.export(store, fmt="json")
            entries = json.loads(text)
            self.assertTrue(entries)
            for entry in entries:
                self.assertEqual(set(entry), {"id", "time", "kind", "payload"})

    def test_markdown_shape(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            text = audit_export.export(store, fmt="md")
            self.assertIn("# Continuum Audit Trail", text)
            self.assertIn("| ID | Time | Kind | Payload |", text)
            self.assertIn("decision", text)

    def test_since_relative_filter(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            # Everything is recent, so 7d keeps it; a far-future-ish bound drops all.
            recent = audit_export.collect_events(store, audit_export.parse_since("7d"))
            self.assertTrue(recent)
            none = audit_export.collect_events(store, "2999-01-01T00:00:00+00:00")
            self.assertEqual(none, [])

    def test_since_invalid_raises(self):
        with self.assertRaises(ValueError):
            audit_export.parse_since("not-a-date")

    def test_cli_export_json_and_md(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            project = store.project
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["audit", "export", "--format", "json", "--project", str(project)]), 0)
            json.loads(out.getvalue())
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["audit", "export", "--format", "md", "--since", "30d", "--project", str(project)]), 0)
            self.assertIn("Audit Trail", out.getvalue())


class NetworkModeTest(unittest.TestCase):
    def _store(self, temporary, network):
        store = MemoryStore(Path(temporary) / "repo")
        store.initialize(1000, 0.8)
        ProviderManager(store.state_dir).ensure_config()
        if network is not None:
            (store.state_dir / "policy.json").write_text(
                json.dumps({"network": network}), encoding="utf-8"
            )
        return store

    def test_on_unchanged_allows_openrouter(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary, "on")
            manager = ProviderManager(store.state_dir, store=store)
            manager.add("openrouter")
            with patch.object(manager, "_json_request", return_value={"data": []}), \
                 patch.dict("os.environ", {"OPENROUTER_API_KEY": "k"}):
                self.assertIn("connected", manager.test("openrouter"))

    def test_local_only_refuses_openrouter_with_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary, "local_only")
            manager = ProviderManager(store.state_dir, store=store)
            manager.add("openrouter")
            with self.assertRaises(ProviderError) as ctx:
                manager.test("openrouter")
            self.assertIn("local_only", str(ctx.exception))
            events = [e for e in store.recent_events(20) if e["kind"] == "policy_network_block"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["payload"]["provider"], "openrouter")
            self.assertEqual(events[0]["payload"]["network_mode"], "local_only")

    def test_local_only_allows_ollama(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary, "local_only")
            manager = ProviderManager(store.state_dir, store=store)
            manager.add("ollama")
            with patch.object(manager, "_json_request", return_value={"data": [{"id": "m"}]}):
                self.assertIn("connected", manager.test("ollama"))
            self.assertEqual(
                [e for e in store.recent_events(20) if e["kind"] == "policy_network_block"], []
            )

    def test_off_refuses_both(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary, "off")
            manager = ProviderManager(store.state_dir, store=store)
            manager.add("openrouter")
            manager.add("ollama")
            with self.assertRaises(ProviderError):
                manager.test("openrouter")
            with self.assertRaises(ProviderError):
                manager.test("ollama")
            blocks = [e for e in store.recent_events(20) if e["kind"] == "policy_network_block"]
            self.assertEqual(len(blocks), 2)

    def test_status_surfaces_network_badge(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary, "local_only")
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["status", "--project", str(store.project)]), 0)
            self.assertIn("Network policy: local_only", out.getvalue())


if __name__ == "__main__":
    unittest.main()
