"""Opt-in sequential execution for configured Continuum teams."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from .core import MemoryStore, compact_text
from .providers import ProviderManager
from .teams import TeamManager


class OrchestrationError(RuntimeError):
    """Raised when a planned workflow cannot safely advance."""


Runner = Callable[[str, str], str]


class Orchestrator:
    def __init__(self, store: MemoryStore, runner: Runner | None = None) -> None:
        self.store = store
        self.teams = TeamManager(store)
        self.providers = ProviderManager(store.state_dir)
        self.runner = runner or self._run_provider

    def plan(self, team_name: str, request: str, task_type: str | None = None) -> dict[str, Any]:
        plan = self.teams.explain(team_name, request, task_type)
        workflow = self.store.create_workflow(team_name, request, plan["task_type"], plan["steps"])
        for step in plan["steps"]:
            task = self.store.create_task(f"[{step['name']}] {request}", "sequential")
            task = self.store.assign_task(task["task_id"], f"{step['name']}:{step['provider']}")
            self.store.update_workflow_step(workflow["workflow_id"], step["order"], "PLANNED", task["task_id"])
        self.store.event(
            "team_workflow_planned",
            {
                "workflow_id": workflow["workflow_id"],
                "team": team_name,
                "task_type": plan["task_type"],
                "request": request,
            },
        )
        return self.store.get_workflow(workflow["workflow_id"]) or workflow

    def execute(
        self,
        team_name: str,
        request: str,
        task_type: str | None = None,
        allowed_files: list[str] | None = None,
        context_mode: str = "compact",
    ) -> dict[str, Any]:
        team = self.teams.load(team_name)
        self.teams.validate(team)
        workflow = self.plan(team_name, request, task_type)
        workflow_id = workflow["workflow_id"]
        allowed = self._normalize_paths(allowed_files or [])
        self.store.update_workflow(workflow_id, "RUNNING")
        for step in workflow["steps"]:
            role = step["role"]
            provider = step["provider"]
            task_id = str(step["task_id"])
            role_config = team["agents"][role]
            output = ""
            try:
                self._begin_step(workflow_id, step["order"], task_id, role, provider, role_config, allowed)
                packet = self.store.context_packet(role, request, context_mode, workflow_id)
                prompt = self._step_prompt(request, role, provider, role_config, packet["text"], allowed)
                output = self.runner(provider, prompt)
                self._verify_writer_paths(role_config, allowed)
                next_role = self._next_role(workflow, step["order"])
                self.store.send_message(role, next_role or "*", output, "result", workflow_id, task_id)
                self.store.update_workflow_step(workflow_id, step["order"], "DONE", output=output)
                self.store.set_task_status(task_id, "DONE", f"{provider} completed step {step['order']}.")
                self.store.update_workflow(workflow_id, "RUNNING", step["order"])
            except Exception as error:
                message = compact_text(str(error), 800)
                if role_config.get("can_edit_files"):
                    try:
                        self._verify_writer_paths(role_config, allowed)
                    except OrchestrationError as unsafe_edit:
                        message = compact_text(f"{message}; {unsafe_edit}", 800)
                retained = compact_text(
                    (f"Provider output:\n{output}\n\n" if output else "") + f"Execution stopped: {message}",
                    2_400,
                )
                self.store.update_workflow_step(workflow_id, step["order"], "FAILED", output=retained)
                self.store.set_task_status(task_id, "FAILED", message)
                self.store.update_workflow(workflow_id, "FAILED", step["order"], message)
                self.store.send_message("continuum", role, message, "error", workflow_id, task_id)
                raise OrchestrationError(message) from error
        summary = f"Sequential workflow completed for {request}."
        return self.store.update_workflow(workflow_id, "DONE", len(workflow["steps"]), summary)

    def _begin_step(
        self,
        workflow_id: str,
        step_number: int,
        task_id: str,
        role: str,
        provider: str,
        role_config: dict[str, Any],
        allowed: set[str],
    ) -> None:
        configured = self.providers.provider(provider)
        if configured.get("kind") == "model" and role_config.get("can_edit_files"):
            raise OrchestrationError(f"Model provider cannot execute as a file editor: {provider}")
        if role_config.get("can_edit_files"):
            if not allowed:
                raise OrchestrationError(
                    f"Writer role {role} requires explicit file claims. Re-run with one or more `--allow-file <path>` arguments."
                )
            existing = self._changed_paths()
            outside = existing - allowed
            if outside:
                paths = ", ".join(sorted(outside))
                raise OrchestrationError(
                    f"Writer role {role} cannot start with unclaimed dirty paths: {paths}. Commit, stash, or include only intended writable files."
                )
            self.store.claim_files(task_id, f"{role}:{provider}", sorted(allowed))
        else:
            self.store.set_task_status(task_id, "RUNNING", f"{role} started.")
        self.store.update_workflow_step(workflow_id, step_number, "RUNNING")

    def _verify_writer_paths(self, role_config: dict[str, Any], allowed: set[str]) -> None:
        if not role_config.get("can_edit_files"):
            return
        outside = self._changed_paths() - allowed
        if outside:
            paths = ", ".join(sorted(outside))
            raise OrchestrationError(
                f"Writing agent changed paths outside its claims: {paths}. Inspect changes before continuing."
            )

    def _run_provider(self, provider: str, prompt: str) -> str:
        configured = self.providers.provider(provider)
        if configured.get("kind") == "model":
            return self.providers.ask(provider, prompt)
        return self.providers.run_agent(provider, prompt, self.store.project)

    def _changed_paths(self) -> set[str]:
        commands = [
            ["git", "-C", str(self.store.project), "diff", "--name-only", "-z", "--"],
            ["git", "-C", str(self.store.project), "diff", "--cached", "--name-only", "-z", "--"],
            ["git", "-C", str(self.store.project), "ls-files", "--others", "--exclude-standard", "-z"],
        ]
        paths: set[str] = set()
        for command in commands:
            completed = subprocess.run(command, capture_output=True, check=False)
            if completed.returncode != 0:
                continue
            for raw in completed.stdout.split(b"\0"):
                if not raw:
                    continue
                value = raw.decode("utf-8", errors="replace").replace("\\", "/")
                if not value.startswith(".continuum/"):
                    paths.add(value)
        return paths

    @staticmethod
    def _normalize_paths(files: list[str]) -> set[str]:
        output = set()
        for item in files:
            value = Path(item).as_posix()
            while value.startswith("./"):
                value = value[2:]
            if value:
                output.add(value)
        return output

    @staticmethod
    def _next_role(workflow: dict[str, Any], order: int) -> str | None:
        for step in workflow["steps"]:
            if int(step["order"]) == order + 1:
                return str(step["role"])
        return None

    @staticmethod
    def _step_prompt(
        request: str,
        role: str,
        provider: str,
        role_config: dict[str, Any],
        packet: str,
        allowed: set[str],
    ) -> str:
        permissions = (
            "You may edit only these claimed files: " + ", ".join(sorted(allowed))
            if role_config.get("can_edit_files")
            else "Read-only step: do not edit project files."
        )
        return (
            f"You are the `{role}` step executed by Continuum using `{provider}`.\n"
            f"Task: {request}\n"
            f"{permissions}\n"
            "Return a concise result for the next agent. Do not repeat all input context.\n\n"
            f"{packet}"
        )
