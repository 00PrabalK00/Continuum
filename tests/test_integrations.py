import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from continuum import integrations
from continuum.cli import main
from continuum.core import MemoryStore


def fresh_store(temporary: str) -> MemoryStore:
    store = MemoryStore(Path(temporary) / "repo")
    store.initialize(100000, 0.8)
    return store


class DetectionTest(unittest.TestCase):
    def test_agents_on_path_are_detected(self):
        with patch("continuum.integrations.shutil.which", lambda name: "/usr/bin/" + name):
            found = {target.id for target in integrations.detect()}
        self.assertIn("claude", found)
        self.assertIn("codex", found)
        self.assertIn("gemini", found)

    def test_agents_md_is_always_a_target(self):
        with (
            patch("continuum.integrations.shutil.which", return_value=None),
            patch("continuum.integrations.home_has", return_value=False),
        ):
            found = {target.id for target in integrations.detect()}
        self.assertEqual(found, {"agents-md"})


class RuleFileTest(unittest.TestCase):
    def test_editor_rules_are_written_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh_store(temporary)
            first = integrations.install_cursor(store)[0]
            path = store.project / ".cursor" / "rules" / "continuum.mdc"
            self.assertEqual(first.status, integrations.INSTALLED)
            self.assertIn("alwaysApply: true", path.read_text(encoding="utf-8"))
            self.assertEqual(integrations.install_cursor(store)[0].status, integrations.ALREADY)

    def test_memory_files_keep_what_is_already_there(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh_store(temporary)
            path = store.project / "AGENTS.md"
            path.write_text("# My own house rules\n\nDo not delete things.\n", encoding="utf-8")
            integrations.append_to_memory_file(path, "test", "test")
            text = path.read_text(encoding="utf-8")
            self.assertIn("My own house rules", text)
            self.assertIn("Continuum Shared Memory", text)

    def test_copilot_gets_the_file_it_actually_reads(self):
        # Copilot is the one common agent that ignores AGENTS.md.
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh_store(temporary)
            first = integrations.install_copilot(store)[0]
            path = store.project / ".github" / "copilot-instructions.md"
            self.assertEqual(first.status, integrations.INSTALLED)
            self.assertIn("get_startup_context", path.read_text(encoding="utf-8"))
            self.assertEqual(integrations.install_copilot(store)[0].status, integrations.ALREADY)

    def test_an_older_block_is_replaced_not_left_alone(self):
        # An upgrade that only reaches fresh installations reaches almost
        # nobody: every project already using Continuum keeps whatever text it
        # was first installed with.
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh_store(temporary)
            path = store.project / "AGENTS.md"
            path.write_text(
                "# House rules\n\nDo not delete things.\n\n"
                "## Continuum Shared Memory\n\n"
                "If the Continuum MCP server is connected, prefer its tools.\n\n"
                "## Recording progress\n\nRecord progress with `save_progress`.\n\n"
                "## My own section\n\nKeep this.\n",
                encoding="utf-8",
            )
            self.assertEqual(integrations.append_to_memory_file(path, "test", "test").status, integrations.UPDATED)
            text = path.read_text(encoding="utf-8")
            self.assertIn("Do not delete things.", text)
            self.assertIn("Keep this.", text)
            self.assertIn("| Where the work stands |", text)
            self.assertNotIn("prefer its tools.\n", text)
            self.assertEqual(text.count(integrations.BLOCK_OPEN), 1)
            self.assertEqual(integrations.append_to_memory_file(path, "test", "test").status, integrations.ALREADY)

    def test_the_version_changes_with_the_text(self):
        # The version is derived rather than bumped by hand, so instructions
        # cannot ship with a stale marker that makes every rerun a no-op.
        self.assertNotEqual(integrations.block_version("one"), integrations.block_version("two"))

    def test_frontmatter_stays_the_first_thing_in_the_file(self):
        # Cursor and Claude only read frontmatter at the very top, so the
        # marker cannot be written above it.
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh_store(temporary)
            integrations.install_cursor(store)
            text = (store.project / ".cursor" / "rules" / "continuum.mdc").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"))
            self.assertLess(text.index("alwaysApply"), text.index(integrations.BLOCK_OPEN))

    def test_copilot_is_detected_without_vs_code(self):
        # Copilot ships in the JetBrains IDEs too, and that machine may never
        # have run VS Code.
        target = next(item for item in integrations.TARGETS if item.id == "copilot")
        with (
            patch("continuum.integrations.shutil.which", return_value=None),
            patch("continuum.integrations.home_has", lambda *names: "JetBrains" in names),
        ):
            self.assertTrue(target.detect())

    def test_every_instruction_names_the_tools_and_the_commands(self):
        # An agent that never connects the MCP server still has to be able to
        # act, so each surface carries both halves.
        text = integrations.AGENT_INSTRUCTIONS
        for tool in ("get_startup_context", "get_latest_handoff", "search_memory", "expand_memory", "save_progress"):
            self.assertIn(tool, text)
        for command in ("continuum search", "continuum save", "continuum handoff", "continuum ask"):
            self.assertIn(command, text)

    def test_windsurf_and_cline_rules_are_written(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh_store(temporary)
            integrations.install_windsurf(store)
            integrations.install_cline(store)
            self.assertTrue((store.project / ".windsurf" / "rules" / "continuum.md").exists())
            self.assertTrue((store.project / ".clinerules" / "continuum.md").exists())


class ClaudeHookTest(unittest.TestCase):
    def test_the_hook_commands_name_no_particular_machine(self):
        # .claude/settings.json is shared team configuration that Continuum
        # merges into rather than owns. An absolute path baked in here either
        # breaks every other clone or forces the whole file out of version
        # control, which is what it did.
        import tempfile as _tempfile

        from continuum.integrations import claude_hooks

        with _tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "repo")
            store.initialize(100000, 0.8)
            hooks = claude_hooks(store)
            commands = [
                hook["command"]
                for phase in ("SessionStart", "SessionEnd")
                for entry in hooks[phase]
                for hook in entry["hooks"]
            ]
            self.assertTrue(commands)
            for command in commands:
                self.assertIn("${CLAUDE_PROJECT_DIR}", command)
                self.assertNotIn(str(store.project), command)

    def test_hooks_are_added_without_disturbing_existing_ones(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh_store(temporary)
            settings_path = store.project / ".claude" / "settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps({"model": "opus", "hooks": {"SessionStart": [{"hooks": [{"command": "mine"}]}]}}),
                encoding="utf-8",
            )
            with patch("continuum.cli.register_claude_mcp", return_value="Claude Code: registered."):
                integrations.install_claude(store)
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(settings["model"], "opus")
            starts = json.dumps(settings["hooks"]["SessionStart"])
            self.assertIn("mine", starts)
            self.assertIn("continuum hook session-start", starts)
            self.assertIn("continuum hook session-end", json.dumps(settings["hooks"]["SessionEnd"]))

    def test_hooks_are_not_added_twice(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh_store(temporary)
            with patch("continuum.cli.register_claude_mcp", return_value="Claude Code: registered."):
                integrations.install_claude(store)
                integrations.install_claude(store)
            settings = json.loads((store.project / ".claude" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(len(settings["hooks"]["SessionStart"]), 1)

    def test_unreadable_settings_are_reported_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh_store(temporary)
            settings_path = store.project / ".claude" / "settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text("{broken", encoding="utf-8")
            with patch("continuum.cli.register_claude_mcp", return_value="Claude Code: registered."):
                results = integrations.install_claude(store)
            self.assertTrue(any(item.status == integrations.SKIPPED for item in results))
            self.assertEqual(settings_path.read_text(encoding="utf-8"), "{broken")

    def test_a_claude_skill_is_written(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh_store(temporary)
            with patch("continuum.cli.register_claude_mcp", return_value="Claude Code: registered."):
                integrations.install_claude(store)
            skill = (store.project / ".claude" / "skills" / "continuum" / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("name: continuum", skill)
            self.assertIn("ask_agent", skill)


class SessionHookCommandTest(unittest.TestCase):
    def test_session_start_prints_the_recorded_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh_store(temporary)
            store.write_handoff("renamed the payment client", "fix the failing retry test")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["hook", "session-start", "--project", str(store.project)]), 0)
            text = output.getvalue()
            self.assertIn("renamed the payment client", text)
            self.assertIn("fix the failing retry test", text)

    def test_session_start_is_silent_for_an_unused_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "untouched"
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["hook", "session-start", "--project", str(project)]), 0)
            self.assertEqual(output.getvalue().strip(), "")

    def test_session_end_records_a_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh_store(temporary)
            store.event("handoff", {"task": "wired the webhook", "next_step": "add retries"})
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["hook", "session-end", "--project", str(store.project)]), 0)
            handoff = (store.project / ".continuum" / "latest_handoff.md").read_text(encoding="utf-8")
            self.assertIn("wired the webhook", handoff)


class InstallCommandTest(unittest.TestCase):
    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["install", "--dry-run", "--project", str(project)]), 0)
            self.assertIn("Would install", output.getvalue())
            self.assertFalse((project / ".cursor").exists())

    def test_only_installs_the_named_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["install", "--only", "cursor", "--project", str(project)]), 0)
            self.assertTrue((project / ".cursor" / "rules" / "continuum.mdc").exists())
            self.assertFalse((project / ".windsurf").exists())

    def test_install_reports_each_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with (
                patch("continuum.integrations.shutil.which", return_value=None),
                patch("continuum.integrations.home_has", return_value=False),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["install", "--project", str(project)]), 0)
            text = output.getvalue()
            self.assertIn("AGENTS.md", text)
            self.assertIn("Nothing else to run", text)

    def test_a_failing_installer_does_not_stop_the_others(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = fresh_store(temporary)

            def boom(_store):
                raise OSError("disk is full")

            broken = integrations.Target("cursor", "Cursor", lambda: True, boom)
            with patch.object(integrations, "TARGETS", [broken, integrations.TARGETS[-1]]):
                results = integrations.install(store)
            self.assertEqual(results[0].status, integrations.SKIPPED)
            self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
