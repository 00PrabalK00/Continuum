import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from continuum.cli import main
from continuum.core import MemoryStore
from continuum.policy import Policy, PolicyError, load_policy, parse_policy, write_starter_policy


class PolicyLoaderTest(unittest.TestCase):
    def test_defaults_when_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / ".continuum"
            state.mkdir()
            policy = load_policy(state)
            self.assertEqual(policy.allowed_providers, [])
            self.assertEqual(policy.denied_files, [])
            self.assertEqual(policy.sensitive_globs, [])
            self.assertTrue(policy.required_tests_before_merge)
            self.assertIsNone(policy.max_context_tokens)
            self.assertIsNone(policy.source)
            # Empty allowed_providers means every provider is allowed.
            self.assertTrue(policy.provider_allowed("claude"))

    def test_rejects_unknown_top_level_key(self):
        with self.assertRaises(PolicyError) as context:
            parse_policy({"allowed_providers": [], "bogus": 1})
        self.assertIn("bogus", str(context.exception))

    def test_rejects_bad_types(self):
        with self.assertRaises(PolicyError):
            parse_policy({"denied_files": "not-a-list"})
        with self.assertRaises(PolicyError):
            parse_policy({"required_tests_before_merge": "yes"})
        with self.assertRaises(PolicyError):
            parse_policy({"max_context_tokens": -5})
        with self.assertRaises(PolicyError):
            parse_policy({"max_context_tokens": True})

    def test_loads_valid_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / ".continuum"
            state.mkdir()
            (state / "policy.json").write_text(
                json.dumps(
                    {
                        "allowed_providers": ["claude"],
                        "denied_files": [".env"],
                        "sensitive_globs": ["**/*.pem"],
                        "max_context_tokens": 5000,
                    }
                ),
                encoding="utf-8",
            )
            policy = load_policy(state)
            self.assertEqual(policy.allowed_providers, ["claude"])
            self.assertTrue(policy.is_denied_file(".env"))
            self.assertTrue(policy.is_sensitive_file("certs/server.pem"))
            self.assertEqual(policy.max_context_tokens, 5000)
            self.assertTrue(policy.source.endswith("policy.json"))

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / ".continuum"
            state.mkdir()
            (state / "policy.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(state)

    def test_glob_matching_variants(self):
        policy = Policy(denied_files=[".env", "**/*.pem", "secrets/**"])
        self.assertTrue(policy.is_denied_file(".env"))
        self.assertTrue(policy.is_denied_file("config/prod.pem"))
        self.assertTrue(policy.is_denied_file("server.pem"))  # **/ also matches top level
        self.assertTrue(policy.is_denied_file("secrets/db/password.txt"))
        self.assertFalse(policy.is_denied_file("src/main.py"))


class PolicyEnforcementTest(unittest.TestCase):
    def _store(self, temporary):
        store = MemoryStore(Path(temporary) / "project")
        store.initialize(1000, 0.8)
        return store

    def test_denied_files_blocks_claim_and_emits_audit_event(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            (store.state_dir / "policy.json").write_text(
                json.dumps({"denied_files": [".env", "secrets/**"]}), encoding="utf-8"
            )
            task = store.create_task("touch secrets")
            with self.assertRaises(ValueError) as context:
                store.claim_files(task["task_id"], "dev:claude", [".env"])
            self.assertIn("denied by policy", str(context.exception))
            events = [item for item in store.recent_events(20) if item["kind"] == "policy_denied_claim"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["payload"]["path"], ".env")

    def test_allowed_providers_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            (store.state_dir / "policy.json").write_text(
                json.dumps({"allowed_providers": ["claude"]}), encoding="utf-8"
            )
            task = store.create_task("scoped work")
            with self.assertRaises(ValueError) as context:
                store.claim_files(task["task_id"], "dev:codex", ["src/main.py"])
            self.assertIn("not allowed by policy", str(context.exception))
            events = [item for item in store.recent_events(20) if item["kind"] == "policy_denied_provider"]
            self.assertEqual(len(events), 1)
            # An allowed provider still works.
            ok = store.claim_files(task["task_id"], "dev:claude", ["src/main.py"])
            self.assertEqual(ok["status"], "RUNNING")

    def test_model_provider_block_intact(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            task = store.create_task("model cannot claim")
            with self.assertRaises(ValueError) as context:
                store.claim_files(task["task_id"], "planner:openrouter", ["src/main.py"])
            self.assertIn("Model provider", str(context.exception))


class PolicyCliTest(unittest.TestCase):
    def test_policy_init_and_show(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            MemoryStore(project).initialize(1000, 0.8)
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["policy", "init", "--project", str(project)]), 0)
                # Refuses to overwrite without --force.
                rc = main(["policy", "init", "--project", str(project)])
            self.assertEqual(rc, 1)
            self.assertTrue((project / ".continuum" / "policy.json").exists())
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["policy", "init", "--project", str(project), "--force"]), 0)
                self.assertEqual(main(["policy", "show", "--project", str(project)]), 0)
            shown = output.getvalue()
            self.assertIn("Policy source:", shown)
            self.assertIn("denied_files", shown)

    def test_write_starter_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / ".continuum"
            state.mkdir()
            write_starter_policy(state)
            with self.assertRaises(PolicyError):
                write_starter_policy(state)
            # Force overwrites cleanly.
            write_starter_policy(state, force=True)


if __name__ == "__main__":
    unittest.main()
