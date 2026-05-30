import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from continuum.cli import main
from continuum.core import MemoryStore
from continuum.secrets_scan import redact_text, scan_text


class SecretsScanTest(unittest.TestCase):
    def _kinds(self, text):
        return {finding.kind for finding in scan_text(text)}

    def test_detects_openai_key(self):
        self.assertIn("openai_key", self._kinds("please use sk-abcdEFGH1234567890wxyz now"))

    def test_detects_aws_access_key(self):
        self.assertIn("aws_access_key", self._kinds("AKIAIOSFODNN7EXAMPLE is the id"))

    def test_detects_github_token(self):
        self.assertIn("github_token", self._kinds("ghp_" + "a" * 36))

    def test_detects_private_key_block(self):
        block = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOwIBAAJB\n-----END RSA PRIVATE KEY-----"
        self.assertIn("private_key", self._kinds(block))

    def test_detects_bearer_token(self):
        self.assertIn("bearer_token", self._kinds("Authorization: Bearer abcdef0123456789ABCDEF"))

    def test_detects_env_style_assignment(self):
        kinds = self._kinds('API_KEY="supersecretvalue12345"')
        self.assertIn("assigned_secret", kinds)

    def test_redact_replaces_with_marker(self):
        clean, findings = redact_text("here is sk-abcdEFGH1234567890wxyz done")
        self.assertIn("[REDACTED:openai_key]", clean)
        self.assertNotIn("sk-abcdEFGH1234567890wxyz", clean)
        self.assertEqual(len(findings), 1)

    def test_redact_assignment_keeps_key_name(self):
        clean, _ = redact_text('password = "hunter2hunter2hunter2"')
        self.assertIn("password", clean)
        self.assertIn("[REDACTED:assigned_secret]", clean)
        self.assertNotIn("hunter2hunter2hunter2", clean)

    def test_no_false_positives_on_normal_source_code(self):
        source = """
def add(a, b):
    # a simple function with an api_key parameter name but no value
    total = a + b
    config = {"timeout": 30, "retries": 3}
    return total

class Repository:
    def __init__(self, url="https://example.com/repo"):
        self.url = url
        self.items = []
"""
        self.assertEqual(scan_text(source), [])

    def test_findings_never_expose_full_secret(self):
        findings = scan_text("sk-abcdEFGH1234567890wxyz")
        self.assertTrue(findings)
        self.assertNotIn("abcdEFGH1234567890", findings[0].preview)


class EgressIntegrationTest(unittest.TestCase):
    def _store(self, temporary):
        store = MemoryStore(Path(temporary) / "project")
        store.initialize(1000, 0.8)
        return store

    def test_context_packet_redacts_secret_and_emits_audit_event(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            store.event("handoff", {"task": "wire auth", "next_step": "ship"})
            store.write_handoff("token is sk-abcdEFGH1234567890wxyz keep going", "ship it")
            packet = store.context_packet("planner", mode="compact")
            self.assertNotIn("sk-abcdEFGH1234567890wxyz", packet["text"])
            self.assertIn("[REDACTED:openai_key]", packet["text"])
            events = [item for item in store.recent_events(30) if item["kind"] == "secret_redacted"]
            self.assertTrue(events)
            self.assertNotIn("sk-", json.dumps(events[-1]["payload"]))
            self.assertIn("openai_key", events[-1]["payload"]["types"])

    def test_sensitive_glob_file_content_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            (store.state_dir / "policy.json").write_text(
                json.dumps({"sensitive_globs": [".env"]}), encoding="utf-8"
            )
            (store.project / ".env").write_text("SECRET=topsecretvalue123456\n", encoding="utf-8")
            (store.project / "main.py").write_text("print('hello world')\n", encoding="utf-8")
            packet = store.context_packet("planner", mode="compact", files=[".env", "main.py"])
            self.assertIn("excluded by policy", packet["text"])
            self.assertNotIn("topsecretvalue123456", packet["text"])
            self.assertIn("hello world", packet["text"])
            events = [item for item in store.recent_events(30) if item["kind"] == "sensitive_excluded"]
            self.assertTrue(events)

    def test_read_context_file_scrubs_included_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            (store.project / "notes.txt").write_text("key sk-abcdEFGH1234567890wxyz", encoding="utf-8")
            content = store.read_context_file("notes.txt")
            self.assertNotIn("sk-abcdEFGH1234567890wxyz", content)
            self.assertIn("[REDACTED:openai_key]", content)


class SecretsCliTest(unittest.TestCase):
    def test_scan_clean_file_exits_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "clean.py"
            path.write_text("def f():\n    return 42\n", encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                rc = main(["secrets", "scan", str(path)])
            self.assertEqual(rc, 0)
            self.assertIn("no secrets", output.getvalue())

    def test_scan_file_with_secret_exits_nonzero_without_full_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "leak.txt"
            path.write_text("api token sk-abcdEFGH1234567890wxyz here", encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                rc = main(["secrets", "scan", str(path)])
            self.assertEqual(rc, 1)
            printed = output.getvalue()
            self.assertIn("openai_key", printed)
            self.assertNotIn("abcdEFGH1234567890", printed)

    def test_scan_missing_file_exits_two(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "nope.txt"
            rc = main(["secrets", "scan", str(missing)])
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
