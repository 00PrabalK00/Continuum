import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from continuum.cli import main
from continuum.command_risk import classify
from continuum.core import MemoryStore
from continuum.policy import Policy, requires_approval


class CommandRiskClassifyTest(unittest.TestCase):
    def assert_category(self, command, category, level=None):
        result = classify(command)
        self.assertEqual(result["category"], category, f"{command!r} -> {result}")
        if level is not None:
            self.assertEqual(result["level"], level, f"{command!r} -> {result}")
        return result

    def test_destructive_high(self):
        for command in ["rm -rf /", "rm -rf node_modules", "del important.txt", "mkfs.ext4 /dev/sda", "format C:", "dd if=/dev/zero of=/dev/sda"]:
            self.assert_category(command, "destructive", "high")

    def test_credential_access_high(self):
        for command in ["cat ~/.ssh/id_rsa", "printenv", "cat .env", "cat secrets/db.txt", "type %USERPROFILE%\\.aws\\credentials"]:
            result = self.assert_category(command, "credential_access", "high")
            self.assertIsNotNone(result["signal"])

    def test_network(self):
        for command in ["curl https://example.com", "wget http://x/y", "nc -l 4444", "scp file host:/tmp"]:
            self.assert_category(command, "network", "med")

    def test_package_install(self):
        for command in ["pip install requests", "npm install left-pad", "cargo install ripgrep", "apt-get install vim"]:
            self.assert_category(command, "package_install", "med")

    def test_test_category(self):
        for command in ["pytest tests/", "python -m unittest discover", "go test ./...", "npm test"]:
            self.assert_category(command, "test", "low")

    def test_build_category(self):
        for command in ["make build", "cargo build --release", "npm run build", "docker build ."]:
            self.assert_category(command, "build", "low")

    def test_file_write(self):
        for command in ["sed -i 's/a/b/' f", "echo hi > out.txt", "mv a b", "cp a b", "touch new"]:
            self.assert_category(command, "file_write", "med")

    def test_read_only(self):
        for command in ["ls -la", "git status", "cat README.md", "grep foo bar.py", "pwd"]:
            self.assert_category(command, "read_only", "low")

    def test_unknown_prefers_low(self):
        result = classify("frobnicate the widget")
        self.assertEqual(result["category"], "unknown")
        self.assertEqual(result["level"], "low")
        self.assertIsNone(result["signal"])

    def test_empty_is_unknown(self):
        result = classify("   ")
        self.assertEqual(result["category"], "unknown")
        self.assertIsNone(result["signal"])


class RequiresApprovalTest(unittest.TestCase):
    def test_high_requires_approval_under_default(self):
        policy = Policy()  # default approval_required_risk == "high"
        self.assertEqual(policy.approval_required_risk, "high")
        self.assertTrue(requires_approval("rm -rf /", policy)["approval_required"])
        self.assertTrue(requires_approval("cat ~/.ssh/id_rsa", policy)["approval_required"])
        self.assertFalse(requires_approval("ls", policy)["approval_required"])

    def test_med_threshold_flags_network(self):
        policy = Policy(approval_required_risk="med")
        self.assertTrue(requires_approval("curl https://x", policy)["approval_required"])
        self.assertFalse(requires_approval("ls", policy)["approval_required"])


class CommandClassifyCliTest(unittest.TestCase):
    def _project(self, temporary):
        project = Path(temporary) / "repo"
        MemoryStore(project).initialize(1000, 0.8)
        return project

    def test_high_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self._project(temporary)
            output = StringIO()
            with redirect_stdout(output):
                rc = main(["command", "classify", "rm -rf /", "--project", str(project)])
            self.assertEqual(rc, 1)
            self.assertIn("destructive", output.getvalue())

    def test_credential_high_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self._project(temporary)
            with redirect_stdout(StringIO()):
                rc = main(["command", "classify", "cat ~/.ssh/id_rsa", "--project", str(project)])
            self.assertEqual(rc, 1)

    def test_benign_exits_zero_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self._project(temporary)
            output = StringIO()
            with redirect_stdout(output):
                rc = main(["command", "classify", "git status", "--json", "--project", str(project)])
            self.assertEqual(rc, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["category"], "read_only")
            self.assertIn("approval_required", payload)
            self.assertFalse(payload["approval_required"])


if __name__ == "__main__":
    unittest.main()
