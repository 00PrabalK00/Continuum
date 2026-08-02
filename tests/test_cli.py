import os
import tempfile
import time
import sys
import unittest
import argparse
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from continuum import agents
from continuum.cli import (
    down,
    finalize_handoff,
    injected_resume_args,
    launches_through_shell,
    main,
    parser,
    pid_is_running,
    shell_safe_context,
    suppress_agent_display_line,
    up,
)
from continuum.core import MemoryStore
from continuum.providers import ProviderError, ProviderManager


class CliTest(unittest.TestCase):
    def test_init_handoff_status_and_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            vault = Path(temporary) / "vault"
            output = StringIO()
            with redirect_stdout(output):
                import sys

                previous = sys.argv
                try:
                    sys.argv = ["continuum", "init", "--project", str(project), "--vault", str(vault)]
                    self.assertEqual(main(), 0)
                    sys.argv = [
                        "continuum",
                        "handoff",
                        "--project",
                        str(project),
                        "--task",
                        "debug build error",
                        "--next-step",
                        "run tests",
                    ]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "search", "--project", str(project), "build error"]
                    self.assertEqual(main(), 0)
                finally:
                    sys.argv = previous
            self.assertIn("debug build error", output.getvalue())

    def test_up_and_down_control_background_daemon(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            args = argparse.Namespace(project=str(project), vault=None)

            self.assertEqual(up(args), 0)
            pid = int((project / ".continuum" / "daemon.pid").read_text(encoding="utf-8"))
            time.sleep(0.1)
            self.assertTrue(pid_is_running(pid))
            self.assertEqual(down(args), 0)

    def test_task_commands_create_claim_and_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                import sys

                previous = sys.argv
                try:
                    sys.argv = ["continuum", "init", "--project", str(project)]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "task", "create", "--project", str(project), "Fix auth"]
                    self.assertEqual(main(), 0)
                    sys.argv = [
                        "continuum", "task", "claim", "--project", str(project), "T0001", "codex", "auth.py"
                    ]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "task", "complete", "--project", str(project), "T0001", "--summary", "Fixed."]
                    self.assertEqual(main(), 0)
                finally:
                    sys.argv = previous
            self.assertIn("T0001 DONE", output.getvalue())

    def test_init_does_not_select_team(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            import sys

            previous = sys.argv
            try:
                sys.argv = ["continuum", "init", "--project", str(project)]
                self.assertEqual(main(), 0)
            finally:
                sys.argv = previous
            self.assertFalse((project / ".continuum" / "teams").exists())

    def test_team_init_and_route_explain(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                import sys

                previous = sys.argv
                try:
                    sys.argv = ["continuum", "init", "--project", str(project)]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "team", "init", "--project", str(project), "default_dev_team"]
                    self.assertEqual(main(), 0)
                    sys.argv = [
                        "continuum", "route", "explain", "--project", str(project),
                        "fix failing auth test",
                    ]
                    self.assertEqual(main(), 0)
                finally:
                    sys.argv = previous
            self.assertIn("Classified route: test_repair", output.getvalue())

    def test_status_reports_release_health_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                import sys

                previous = sys.argv
                try:
                    sys.argv = ["continuum", "init", "--project", str(project)]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "status", "--project", str(project)]
                    self.assertEqual(main(), 0)
                finally:
                    sys.argv = previous
            rendered = output.getvalue()
            for label in ("Project path:", "Daemon state:", "SQLite state:", "MCP availability:", "Configured providers:", "Embedding count:", "Latest handoff path:"):
                self.assertIn(label, rendered)

    def test_team_run_output_is_explicitly_planning_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                import sys

                previous = sys.argv
                try:
                    sys.argv = ["continuum", "init", "--project", str(project)]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "team", "init", "--project", str(project), "fast_bugfix"]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "team", "run", "--project", str(project), "fast_bugfix", "fix login crash"]
                    self.assertEqual(main(), 0)
                finally:
                    sys.argv = previous
            self.assertIn("Automatic provider launching was not requested.", output.getvalue())
            self.assertIn("Workflow planned: W0001.", output.getvalue())

    def test_context_and_message_commands_are_bounded_and_visible(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                import sys

                previous = sys.argv
                try:
                    sys.argv = ["continuum", "init", "--project", str(project)]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "message", "send", "--project", str(project), "explorer", "coder", "check auth"]
                    self.assertEqual(main(), 0)
                    sys.argv = ["continuum", "context", "build", "--project", str(project), "coder", "--mode", "compact"]
                    self.assertEqual(main(), 0)
                finally:
                    sys.argv = previous
            rendered = output.getvalue()
            self.assertIn("MSG0001", rendered)
            self.assertIn("Estimated context:", rendered)
            self.assertIn("check auth", rendered)

    def test_instruct_command_creates_graph_backed_execution_packet(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                previous = sys.argv
                try:
                    sys.argv = ["continuum", "init", "--project", str(project)]
                    self.assertEqual(main(), 0)
                    sys.argv = [
                        "continuum",
                        "instruct",
                        "--project",
                        str(project),
                        "--planner",
                        "claude-opus-4-1-20250805",
                        "--executor",
                        "codex",
                        "--mode",
                        "checkpoint",
                        "--scope",
                        "continuum/terminal.py",
                        "--goal",
                        "Implement deterministic PTY receipt validation",
                    ]
                    self.assertEqual(main(), 0)
                finally:
                    sys.argv = previous
            rendered = output.getvalue()
            self.assertIn("Delegation planned: D0001", rendered)
            self.assertIn("Executor: codex", rendered)
            self.assertIn("Packet:", rendered)
            self.assertTrue((project / ".continuum" / "delegations" / "D0001" / "graph.json").exists())

    def test_resume_injects_context_with_agent_specific_prompt_mode(self):
        prompt = "continue from handoff"

        self.assertEqual(injected_resume_args("claude", ["--model", "opus"], prompt)[-1], prompt)
        self.assertEqual(injected_resume_args("codex", [], prompt), ["exec", prompt])
        self.assertEqual(injected_resume_args("gemini", ["--approval-mode", "plan"], prompt)[:2], ["--prompt", prompt])
        self.assertIn("--output-format", injected_resume_args("gemini", [], prompt))
        merged = injected_resume_args("gemini", ["--prompt", "other"], prompt)
        self.assertIn(prompt, merged[1])
        self.assertIn("other", merged[1])

    def test_known_gemini_startup_noise_is_hidden_from_shell_display(self):
        self.assertTrue(suppress_agent_display_line("gemini", "Warning: 256-color support not detected.\n"))
        self.assertTrue(suppress_agent_display_line("gemini", "Ripgrep is not available. Falling back to GrepTool.\n"))
        self.assertFalse(suppress_agent_display_line("gemini", "Actual answer.\n"))
        self.assertFalse(suppress_agent_display_line("codex", "Warning: 256-color support not detected.\n"))

    def test_semantic_retrieval_falls_back_to_exact_search_when_ollama_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            import sys

            previous = sys.argv
            try:
                sys.argv = ["continuum", "init", "--project", str(project)]
                self.assertEqual(main(), 0)
                from continuum.core import MemoryStore

                MemoryStore(project).event("decision", {"summary": "auth retry behavior"})
                with patch.object(ProviderManager, "embed", side_effect=ProviderError("connection refused")):
                    with redirect_stdout(output):
                        sys.argv = ["continuum", "memory", "retrieve", "--project", str(project), "auth", "--semantic"]
                        self.assertEqual(main(), 0)
            finally:
                sys.argv = previous
            self.assertIn("Falling back to exact local search", output.getvalue())
            self.assertIn("auth retry behavior", output.getvalue())

    def test_model_ask_accepts_unquoted_multi_word_prompt(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            previous = sys.argv
            try:
                with patch.object(ProviderManager, "ask", return_value="ok") as ask:
                    with redirect_stdout(output):
                        sys.argv = [
                            "continuum",
                            "model",
                            "ask",
                            "--project",
                            str(project),
                            "ollama",
                            "summarize",
                            "this",
                            "large",
                            "paste",
                        ]
                        self.assertEqual(main(), 0)
            finally:
                sys.argv = previous

            ask.assert_called_once()
            self.assertEqual(ask.call_args.args[1], "summarize this large paste")
            self.assertIn("ok", output.getvalue())

    def test_chat_sends_message_with_context_to_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            previous = sys.argv
            try:
                sys.argv = ["continuum", "init", "--project", str(project)]
                self.assertEqual(main(), 0)
                with patch("continuum.cli.run_agent", return_value=0) as run:
                    with redirect_stdout(output):
                        sys.argv = [
                            "continuum",
                            "chat",
                            "--project",
                            str(project),
                            "claude",
                            "hi",
                            "there",
                        ]
                        self.assertEqual(main(), 0)
            finally:
                sys.argv = previous

            args = run.call_args.args[0]
            prompt = run.call_args.kwargs["injected_context"]
            self.assertEqual(args.agent, "claude")
            self.assertEqual(args.agent_args, [])
            self.assertTrue(run.call_args.kwargs["resumed"])
            self.assertIn("User message:\nhi there", prompt)
            self.assertIn("Do not run shell commands", prompt)
            self.assertIn("satisfies the project startup requirement", prompt)
            self.assertIn("plain Continuum chat question", prompt)
            self.assertIn("Chat target: claude", output.getvalue())

    def test_chat_accepts_explicit_context_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            previous = sys.argv
            try:
                sys.argv = ["continuum", "init", "--project", str(project)]
                self.assertEqual(main(), 0)
                with patch("continuum.cli.run_agent", return_value=0) as run:
                    sys.argv = [
                        "continuum",
                        "chat",
                        "--project",
                        str(project),
                        "gemini",
                        "normal",
                        "inspect",
                        "rules",
                    ]
                    self.assertEqual(main(), 0)
            finally:
                sys.argv = previous

            args = run.call_args.args[0]
            prompt = run.call_args.kwargs["injected_context"]
            self.assertEqual(args.agent, "gemini")
            self.assertTrue(run.call_args.kwargs["resumed"])
            self.assertIn("inspect rules", prompt)

    def test_codex_chat_uses_noninteractive_exec_subcommand(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            previous = sys.argv
            try:
                sys.argv = ["continuum", "init", "--project", str(project)]
                self.assertEqual(main(), 0)
                with patch("continuum.cli.shutil.which", return_value="codex"):
                    with patch("continuum.cli.subprocess.Popen") as popen:
                        process = popen.return_value
                        process.stdout = []
                        process.wait.return_value = 0
                        sys.argv = ["continuum", "chat", "--project", str(project), "codex", "hi"]
                        self.assertEqual(main(), 0)
            finally:
                sys.argv = previous

            command = popen.call_args.args[0]
            self.assertIn("codex", Path(command[0]).name)
            self.assertEqual(command[1], "exec")

    def test_memory_refresh_embeds_recent_events_with_event_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            import sys

            previous = sys.argv
            try:
                sys.argv = ["continuum", "init", "--project", str(project)]
                self.assertEqual(main(), 0)
                with patch.object(ProviderManager, "embed", return_value=("embed", [1.0, 0.0])):
                    sys.argv = ["continuum", "memory", "refresh", "--project", str(project), "--limit", "1"]
                    self.assertEqual(main(), 0)
            finally:
                sys.argv = previous
            from continuum.core import MemoryStore

            self.assertTrue(MemoryStore(project).semantic_search([1.0, 0.0])[0]["memory_id"].startswith("M"))

    def test_interactive_run_records_terminal_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with (
                patch("continuum.cli.agent_command", return_value=["agent"]),
                patch("continuum.cli.terminal_backend", return_value="pty"),
                patch("continuum.cli.run_terminal_process", side_effect=lambda *args, **kwargs: kwargs["on_output"]("ready\r\n") or 0),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["run", "--project", str(project), "--interactive", "codex"]), 0)
            usage = (project / ".continuum" / "token_usage.json").read_text(encoding="utf-8")
            self.assertIn('"terminal": "pty"', usage)
            self.assertIn('"adapter": "codex_interactive"', usage)
            self.assertIn('"adapter_phase": "EXITED"', usage)
            self.assertIn("Interactive terminal backend: pty", output.getvalue())
            self.assertIn("Interactive adapter: codex_interactive", output.getvalue())

    def test_adapters_list_describes_bounded_interactive_behavior(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["adapters", "list"]), 0)
        rendered = output.getvalue()
        self.assertIn("claude: claude_interactive", rendered)
        self.assertIn("codex: codex_interactive", rendered)
        self.assertIn("gemini: gemini_interactive", rendered)
        self.assertIn("never auto-approves", rendered)

    def test_worktree_resume_runs_agent_inside_isolated_worktree(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            import subprocess

            project.mkdir()
            subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
            (project / "src").mkdir()
            (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Continuum Test", "-c", "user.email=test@example.invalid", "commit", "-m", "base"],
                cwd=project,
                capture_output=True,
                check=True,
            )
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["init", "--project", str(project)]), 0)
                self.assertEqual(
                    main([
                        "worktree", "schedule", "--project", str(project),
                        "split work", "--lane", "backend:codex:src",
                    ]),
                    0,
                )
            captured = {}

            def fake_run(args, resumed=False, injected_context=None):
                captured["cwd"] = str(args.cwd)
                captured["resumed"] = resumed
                captured["context"] = injected_context
                return 0

            with patch("continuum.cli.run_agent", side_effect=fake_run):
                self.assertEqual(main(["worktree", "resume", "--project", str(project), "T0001", "codex"]), 0)
            self.assertIn(".continuum", captured["cwd"])
            self.assertTrue(captured["resumed"])
            self.assertIn("Parallel Worktree Context", captured["context"])

    def test_interactive_terminal_missing_backend_prints_specific_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            errors = StringIO()
            from continuum.terminal import TerminalUnavailable

            with (
                patch("continuum.cli.agent_command", return_value=["agent"]),
                patch("continuum.cli.terminal_backend", return_value="unavailable"),
                patch(
                    "continuum.cli.run_terminal_process",
                    side_effect=TerminalUnavailable(
                        "Interactive terminal mode on Windows requires pywinpty. "
                        "Run `py -m pip install pywinpty`, then retry with `--interactive`."
                    ),
                ),
                redirect_stderr(errors),
            ):
                self.assertEqual(main(["run", "--project", str(project), "--interactive", "codex"]), 1)
            self.assertIn("py -m pip install pywinpty", errors.getvalue())

    def test_session_commands_show_detected_and_published_external_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            attached = {
                "session": {"session_id": "S0001", "status": "ATTACHED", "agent": "claude", "pid": 42, "cwd": str(project)},
                "packet": {"path": str(project / ".continuum" / "external_sessions" / "S0001" / "context.md"), "estimated_tokens": 70, "mode": "compact"},
            }
            with (
                patch("continuum.cli.ExternalSessionManager.detect", return_value=[{"pid": 42, "agent": "claude", "project_match": True, "cwd": str(project)}]),
                patch("continuum.cli.ExternalSessionManager.attach", return_value=attached),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["session", "detect", "--project", str(project)]), 0)
                self.assertEqual(main(["session", "attach", "--project", str(project), "42"]), 0)
            rendered = output.getvalue()
            self.assertIn("PID=42 claude project-match", rendered)
            self.assertIn("Context packet published:", rendered)
            self.assertIn("cannot retroactively capture or type", rendered)

    def test_logs_shows_no_log_message_when_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["logs", "--project", str(project)]), 0)
            self.assertIn("No daemon output log", output.getvalue())

    def test_down_returns_zero_when_daemon_not_running(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            args = argparse.Namespace(project=str(project), vault=None)
            self.assertEqual(down(args), 0)

    def test_task_list_shows_no_tasks_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["init", "--project", str(project)]), 0)
                self.assertEqual(main(["task", "list", "--project", str(project)]), 0)
            self.assertIn("No tasks found", output.getvalue())

    def test_providers_list_shows_configured_providers(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["init", "--project", str(project)]), 0)
                self.assertEqual(main(["providers", "add", "--project", str(project), "ollama"]), 0)
                self.assertEqual(main(["providers", "list", "--project", str(project)]), 0)
            self.assertIn("ollama", output.getvalue())
            self.assertIn("enabled", output.getvalue())


class SimpleFrontDoorTest(unittest.TestCase):
    def test_save_splits_task_and_next_step(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(["save", "--project", str(project), "fixed auth bug | test the retry logic"]), 0
                )
            text = output.getvalue()
            self.assertIn("Saved: fixed auth bug", text)
            self.assertIn("test the retry logic", text)
            handoff = (project / ".continuum" / "latest_handoff.md").read_text(encoding="utf-8")
            self.assertIn("fixed auth bug", handoff)
            self.assertIn("test the retry logic", handoff)

    def test_save_auto_initializes_uninitialized_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["save", "--project", str(project), "first note"]), 0)
            self.assertTrue((project / ".continuum" / "config.json").exists())

    def test_save_without_text_or_history_explains_usage(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            with redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(["save", "--project", str(project)])
            self.assertIn("continuum save", str(raised.exception))

    def test_copy_prints_paste_ready_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                self.assertEqual(main(["save", "--project", str(project), "renamed the API client"]), 0)
                with patch("continuum.cli.copy_to_clipboard", return_value=False):
                    self.assertEqual(main(["copy", "--project", str(project)]), 0)
            text = output.getvalue()
            self.assertIn("previous AI session", text)
            self.assertIn("renamed the API client", text)
            self.assertIn("copy the text above manually", text)

    def test_bare_invocation_prints_status_card(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            project.mkdir()
            previous = Path.cwd()
            output = StringIO()
            try:
                os.chdir(project)
                with redirect_stdout(output):
                    self.assertEqual(main([]), 0)
            finally:
                os.chdir(previous)
            text = output.getvalue()
            self.assertIn("not initialized", text)
            self.assertIn("continuum go", text)

    def test_bare_invocation_shows_saved_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            project.mkdir()
            previous = Path.cwd()
            output = StringIO()
            try:
                os.chdir(project)
                with redirect_stdout(output):
                    self.assertEqual(main(["save", "fix login timeout | rerun login test"]), 0)
                    self.assertEqual(main([]), 0)
            finally:
                os.chdir(previous)
            text = output.getvalue()
            self.assertIn("Task: fix login timeout", text)
            self.assertIn("Next: rerun login test", text)
            self.assertIn("continuum go", text)

    def test_setup_initializes_and_reports_missing_clis(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                with patch("continuum.cli.shutil.which", return_value=None):
                    self.assertEqual(main(["setup", "--project", str(project)]), 0)
            text = output.getvalue()
            self.assertIn("Agent CLIs found: none", text)
            self.assertIn("Daily commands", text)
            self.assertTrue((project / ".continuum" / "config.json").exists())


class AgentRegistryTest(unittest.TestCase):
    def store(self, project: Path) -> MemoryStore:
        store = MemoryStore(project)
        store.initialize(100000, 0.8)
        return store

    def test_unknown_cli_on_path_is_adopted_with_the_default_convention(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(Path(temporary) / "repo")
            with patch("continuum.agents.shutil.which", return_value="/usr/bin/hermes"):
                spec = agents.resolve(store, "hermes")
            self.assertEqual(spec["command"], "hermes")
            self.assertEqual(spec["inject"], "arg")
            self.assertIn("hermes", agents.read_custom(store))
            self.assertEqual(agents.launch_args(spec, [], "CONTEXT")[-1], "CONTEXT")

    def test_unknown_cli_not_on_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(Path(temporary) / "repo")
            with patch("continuum.agents.shutil.which", return_value=None):
                with self.assertRaises(agents.AgentError):
                    agents.resolve(store, "nope")

    def test_registered_spec_controls_prompt_injection(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(Path(temporary) / "repo")
            agents.write_agent(store, "opencode", {"inject": "flag", "flag": "--task"})
            spec = agents.read_agents(store)["opencode"]
            self.assertEqual(agents.launch_args(spec, [], "CONTEXT")[:2], ["--task", "CONTEXT"])
            agents.write_agent(store, "runner", {"inject": "stdin"})
            piped = agents.read_agents(store)["runner"]
            self.assertEqual(agents.launch_args(piped, ["--quiet"], "CONTEXT"), ["--quiet"])
            self.assertEqual(agents.stdin_prompt(piped, "CONTEXT"), "CONTEXT")

    def test_builtin_conventions_are_preserved(self):
        prompt = "CONTEXT"
        self.assertEqual(injected_resume_args("codex", [], prompt), ["exec", prompt])
        self.assertEqual(injected_resume_args("gemini", [], prompt)[:2], ["--prompt", prompt])
        self.assertEqual(injected_resume_args("claude", ["--model", "opus"], prompt)[-1], prompt)

    def test_pick_agent_prefers_an_agent_other_than_the_last_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(Path(temporary) / "repo")
            with patch("continuum.agents.shutil.which", return_value="/usr/bin/agent"):
                self.assertEqual(agents.pick_agent(store, exclude="claude"), "codex")
                self.assertEqual(agents.pick_agent(store, exclude=None), "claude")

    def test_pick_agent_without_any_installed_cli_explains_the_fix(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(Path(temporary) / "repo")
            with patch("continuum.agents.shutil.which", return_value=None):
                with self.assertRaises(agents.AgentError) as caught:
                    agents.pick_agent(store)
            self.assertIn("continuum copy", str(caught.exception))

    def test_agent_add_and_list_report_the_registered_spec(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(["agent", "add", "hermes", "--inject", "subcommand", "--subcommand", "run",
                          "--project", str(project)]),
                    0,
                )
                self.assertEqual(main(["agent", "list", "--project", str(project)]), 0)
            text = output.getvalue()
            self.assertIn("Registered agent: hermes", text)
            self.assertIn("hermes: subcommand run (project", text)


class ExitHandoffTest(unittest.TestCase):
    def test_session_exit_writes_a_handoff_without_a_manual_save(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with (
                patch("continuum.cli.agent_command", return_value=[sys.executable, "-c", "print('worked')"]),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["run", "--project", str(project), "claude"]), 0)
            text = output.getvalue()
            self.assertIn("Saved:", text)
            self.assertIn("continuum go", text)
            self.assertTrue((project / ".continuum" / "latest_handoff.md").exists())

    def test_nonzero_exit_is_recorded_as_the_next_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            output = StringIO()
            with (
                patch("continuum.cli.agent_command", return_value=[sys.executable, "-c", "raise SystemExit(3)"]),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["run", "--project", str(project), "claude"]), 3)
            handoff = (project / ".continuum" / "latest_handoff.md").read_text(encoding="utf-8")
            self.assertIn("exited with code 3", handoff)

    def test_checkpoint_handoff_is_not_overwritten_on_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            store = MemoryStore(project)
            store.initialize(100000, 0.8)
            store.write_handoff("checkpoint task", "checkpoint next")
            with redirect_stdout(StringIO()):
                finalize_handoff(store, "claude", "session-1", ["output\n"], 0, True)
            handoff = (project / ".continuum" / "latest_handoff.md").read_text(encoding="utf-8")
            self.assertIn("checkpoint task", handoff)


class ShellShimContextTest(unittest.TestCase):
    """Windows runs .cmd/.bat shims through cmd.exe, which ends the command
    line at the first newline. A multi-line handoff passed as an argument
    arrives truncated to its first line, so those agents get a pointer to the
    handoff file instead."""

    def test_shim_executables_are_detected(self):
        with patch("continuum.cli.shutil.which", return_value=r"C:\npm\gemini.CMD"):
            self.assertTrue(launches_through_shell({"command": "gemini"}))
        with patch("continuum.cli.shutil.which", return_value=r"C:\bin\codex.EXE"):
            self.assertFalse(launches_through_shell({"command": "codex"}))

    def test_shim_context_is_one_line_and_names_the_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            store = MemoryStore(project)
            store.initialize(100000, 0.8)
            store.write_handoff("fix checkout", "add a regression test")
            context = shell_safe_context(store, "line one\nline two\nline three")
            self.assertNotIn("\n", context)
            self.assertIn("latest_handoff.md", context)

    def test_multiline_context_is_not_passed_as_an_argument_to_a_shim(self):
        captured: dict[str, list[str]] = {}

        def record(agent, passthrough):
            captured["args"] = list(passthrough)
            return [sys.executable, "-c", "pass"]

        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            store = MemoryStore(project)
            store.initialize(100000, 0.8)
            store.write_handoff("fix checkout", "add a regression test")
            output = StringIO()
            with (
                patch("continuum.cli.agent_command", side_effect=record),
                patch("continuum.cli.launches_through_shell", return_value=True),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["resume", "--project", str(project), "claude"]), 0)
            self.assertTrue(captured["args"], "the agent received no arguments")
            for value in captured["args"]:
                self.assertNotIn("\n", value)
            self.assertIn("shell shim", output.getvalue())

    def test_stdin_agents_keep_the_full_context_through_a_shim(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            store = MemoryStore(project)
            store.initialize(100000, 0.8)
            agents.write_agent(store, "piped", {"inject": "stdin"})
            output = StringIO()
            script = "import sys; data = sys.stdin.read(); print('CHARS', len(data))"
            with (
                patch("continuum.cli.agent_command", return_value=[sys.executable, "-c", script]),
                patch("continuum.cli.launches_through_shell", return_value=True),
                redirect_stdout(output),
            ):
                self.assertEqual(main(["resume", "--project", str(project), "piped"]), 0)
            text = output.getvalue()
            self.assertNotIn("shell shim", text)
            chars = int(text.split("CHARS")[1].split()[0])
            self.assertGreater(chars, 200)


class HelpSurfaceTest(unittest.TestCase):
    def test_top_level_help_lists_only_the_daily_commands(self):
        text = parser().format_help()
        self.assertIn("{install,go,copy,help}", text)
        self.assertNotIn("flight-record", text)
        self.assertNotIn("pr-packet", text)
        self.assertIn("continuum help --all", text)

    def test_help_all_still_lists_the_advanced_surface(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["help", "--all"]), 0)
        text = output.getvalue()
        self.assertIn("worktree", text)
        self.assertIn("flight-record", text)

    def test_hidden_commands_still_run(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["adapters", "list"]), 0)
        self.assertIn("claude", output.getvalue())


if __name__ == "__main__":
    unittest.main()
