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

    def test_latest_task_prefers_meaningful_handoff_over_wrapped_session_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "app")
            store.initialize(1000, 0.8)
            store.event("handoff", {"task": "Tune training_v2 routing", "next_step": "run step8"})
            store.event(
                "handoff",
                {
                    "task": "Wrapped `gemini` session `20260530-225758-gemini` completed.",
                    "next_step": "Review the output and record the next action.",
                },
            )

            task = store.latest_task()

            assert task is not None
            self.assertEqual(task[0], "Tune training_v2 routing")
            self.assertEqual(task[1], "run step8")

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

    def test_hierarchical_delegation_stores_graph_and_execution_packet(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "app")
            store.initialize(1000, 0.8)

            delegation = store.create_delegation(
                "claude-opus-4-1-20250805",
                "codex",
                "Implement deterministic PTY input receipt validation",
                mode="checkpoint",
                scope=["continuum/terminal.py", "tests/test_terminal.py"],
                review="each-step",
            )

            self.assertEqual(delegation["delegation_id"], "D0001")
            self.assertEqual(delegation["graph"]["kind"], "hierarchical_model_delegation")
            self.assertIn({"from": "agent:claude-opus-4-1-20250805", "to": f"packet:{delegation['task_id']}", "type": "plans"}, delegation["graph"]["edges"])
            self.assertTrue((store.project / ".continuum" / "delegations" / "D0001" / "graph.json").exists())
            packet = Path(delegation["packet_path"]).read_text(encoding="utf-8")
            self.assertIn("Task ID:", packet)
            self.assertIn("Reason This Task Exists", packet)
            self.assertIn("Files Allowed To Edit", packet)
            self.assertIn("When To Escalate Back To Big Model", packet)
            self.assertIn("continuum/terminal.py", packet)
            self.assertIn("tests/test_terminal.py", packet)
            inbox = store.messages("codex")
            self.assertEqual(inbox[0]["kind"], "execution_packet")

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

    def test_semantic_search_returns_event_attribution_and_task_weighting(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "app")
            store.initialize(1000, 0.8)
            store.store_embedding("M:12", "ollama", "embed", [1.0, 0.0], "authentication callback finding")
            store.store_embedding("M:13", "ollama", "embed", [1.0, 0.0], "unrelated finding")

            results = store.semantic_search([1.0, 0.0], task_hint="authentication")

            self.assertEqual(results[0]["memory_id"], "M12")
            self.assertEqual(results[0]["source"], "event:12")

    def test_external_session_context_is_persisted_and_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "app")
            store.initialize(1000, 0.8)
            session = store.register_external_session(88, 12.5, "codex", str(store.project), "codex")

            packet = store.publish_external_session_context(session["session_id"], "compact")

            self.assertEqual(session["session_id"], "S0001")
            self.assertLessEqual(packet["estimated_tokens"], 1_000)
            self.assertTrue(Path(packet["path"]).exists())


if __name__ == "__main__":
    unittest.main()
