import tempfile
import unittest
import json
from pathlib import Path

from continuum.core import MemoryStore, compact_text, project_key


class MemoryStoreTest(unittest.TestCase):
    def test_init_creates_project_memory_and_project_scoped_obsidian_notes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "app"
            vault = root / "vault"
            store = MemoryStore(project, vault)

            store.initialize(1000, 0.8)

            self.assertTrue((project / ".continuum" / "latest_handoff.md").exists())
            self.assertTrue((project / ".continuum" / "current.md").exists())
            self.assertTrue((vault / "Projects" / project_key(project) / "Latest Handoff.md").exists())
            self.assertTrue((vault / "Memory Index.md").exists())
            self.assertIn("/Current|", (vault / "Memory Index.md").read_text(encoding="utf-8"))
            self.assertIn("Continuum Shared Memory", (project / "AGENTS.md").read_text(encoding="utf-8"))

    def test_handoff_is_bounded_and_searchable(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "app"
            store = MemoryStore(project)
            store.initialize(1000, 0.8)
            store.event("handoff", {"task": "fix authentication retry", "next_step": "run tests"})
            store.write_handoff("fix authentication retry " + "x" * 5000, "run tests")

            handoff = (project / ".continuum" / "latest_handoff.md").read_text(encoding="utf-8")
            self.assertIn("content truncated", handoff)
            self.assertEqual(len(store.search("authentication")), 1)

    def test_generated_obsidian_notes_do_not_retrigger_watcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "Agents"
            store = MemoryStore(project, project)
            store.initialize(1000, 0.8)
            store.poll_changes()
            store.write_handoff("refresh generated note", "continue")

            changes = store.poll_changes()

            self.assertFalse(any("Projects" in item or "Memory Index.md" in item for item in changes))

    def test_compact_text_caps_output(self):
        text = compact_text("z" * 2000, 100)
        self.assertLessEqual(len(text), 100)

    def test_resume_modes_are_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "app")
            store.initialize(1000, 0.8)
            store.write_handoff("active task " + ("x" * 10000), "continue")
            self.assertLessEqual(len(store.resume_context("compact")), 800 * 4)
            self.assertLessEqual(len(store.resume_context("normal")), 2000 * 4)
            self.assertLessEqual(len(store.resume_context("deep")), 6000 * 4)

    def test_task_file_claims_reject_conflicts_and_release_when_done(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "app")
            store.initialize(1000, 0.8)
            first = store.create_task("Edit auth", "sequential")
            second = store.create_task("Edit UI", "parallel")
            store.claim_files(first["task_id"], "codex", ["src/auth.ts"])

            with self.assertRaises(ValueError):
                store.claim_files(second["task_id"], "gemini", ["src/auth.ts"])

            store.set_task_status(first["task_id"], "DONE", "Patched auth.")
            claimed = store.claim_files(second["task_id"], "gemini", ["src/auth.ts"])
            self.assertEqual(claimed["status"], "RUNNING")

    def test_task_claim_preserves_hidden_directory_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "app")
            store.initialize(1000, 0.8)
            task = store.create_task("Edit CI")

            claimed = store.claim_files(task["task_id"], "codex", [".github/workflows/test.yml"])

            self.assertEqual(claimed["locked_files"][0]["path"], ".github/workflows/test.yml")

    def test_model_provider_cannot_claim_files_through_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "app")
            store.initialize(1000, 0.8)
            task = store.create_task("Text-only reasoning")

            with self.assertRaisesRegex(ValueError, "Model provider cannot claim"):
                store.claim_files(task["task_id"], "reasoner:openrouter", ["src/auth.py"])

    def test_configured_custom_model_provider_cannot_claim_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "app")
            store.initialize(1000, 0.8)
            providers = store.state_dir / "providers.json"
            providers.write_text(json.dumps({"providers": {"gateway": {"kind": "model"}}}), encoding="utf-8")
            task = store.create_task("Text-only custom provider")

            with self.assertRaisesRegex(ValueError, "gateway"):
                store.claim_files(task["task_id"], "planner:gateway", ["src/auth.py"])

    def test_workflow_messages_produce_bounded_role_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "app")
            store.initialize(1000, 0.8)
            workflow = store.create_workflow(
                "team", "fix auth", "bug_fix", [{"order": 1, "name": "coder", "provider": "codex"}]
            )
            store.send_message("explorer", "coder", "Relevant auth finding " + ("x" * 10000), workflow_ref=workflow["workflow_id"])

            packet = store.context_packet("coder", "auth", "compact", workflow["workflow_id"])

            self.assertIn("Relevant auth finding", packet["text"])
            self.assertLessEqual(len(packet["text"]), 800 * 4)

    def test_semantic_search_ranks_stored_embeddings(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "app")
            store.initialize(1000, 0.8)
            store.store_embedding("near", "ollama", "embed", [1.0, 0.0], "near result")
            store.store_embedding("far", "ollama", "embed", [0.0, 1.0], "far result")

            results = store.semantic_search([0.9, 0.1])

            self.assertEqual(results[0]["memory_key"], "near")

    def test_semantic_search_skips_corrupt_vector_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "app")
            store.initialize(1000, 0.8)
            connection = store.connect()
            connection.execute(
                "INSERT INTO embeddings(memory_key, created_at, provider, model, vector, source_preview) VALUES (?, ?, ?, ?, ?, ?)",
                ("bad", "now", "ollama", "embed", "{broken", "bad row"),
            )
            connection.commit()
            connection.close()

            self.assertEqual(store.semantic_search([1.0]), [])


if __name__ == "__main__":
    unittest.main()
