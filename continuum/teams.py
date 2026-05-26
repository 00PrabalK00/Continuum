"""Continuum Teams configuration and deterministic workflow planning."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .core import CONTEXT_BUDGETS, MemoryStore, write_text

PROVIDERS = {"claude_code", "gemini_cli", "codex", "ollama", "openrouter"}
MODEL_PROVIDERS = {"ollama", "openrouter"}

DEFAULT_DEV_TEAM: dict[str, Any] = {
    "team_name": "default_dev_team",
    "mode": "orchestrated",
    "memory": {
        "strategy": "lazy_context",
        "startup_context_tokens": CONTEXT_BUDGETS["startup"],
        "retrieval_tokens": CONTEXT_BUDGETS["retrieval_default"],
        "handoff_tokens": CONTEXT_BUDGETS["handoff"],
    },
    "agents": {
        "explorer": {"provider": "gemini_cli", "role": "codebase_exploration", "can_edit_files": False, "can_run_commands": True},
        "reasoner": {"provider": "openrouter", "role": "architecture_reasoning", "can_edit_files": False, "can_run_commands": False},
        "coder": {"provider": "claude_code", "role": "implementation", "can_edit_files": True, "can_run_commands": True},
        "tester": {"provider": "codex", "role": "test_and_patch", "can_edit_files": True, "can_run_commands": True},
        "memory_worker": {"provider": "ollama", "role": "memory_compression", "can_edit_files": False, "can_run_commands": False},
    },
    "routing": {
        "new_feature": ["explorer", "reasoner", "coder", "tester", "memory_worker"],
        "bug_fix": ["explorer", "reasoner", "coder", "tester", "memory_worker"],
        "large_refactor": ["explorer", "reasoner", "coder", "tester", "reasoner", "memory_worker"],
        "documentation": ["explorer", "reasoner", "memory_worker"],
        "handoff": ["memory_worker"],
        "test_repair": ["tester", "memory_worker"],
    },
    "safety": {
        "one_writer_at_a_time": True,
        "require_tests_after_code_change": True,
        "block_conflicting_file_edits": True,
        "ask_user_before_large_refactor": True,
    },
}

LOCAL_ONLY_TEAM: dict[str, Any] = {
    "team_name": "local_only",
    "mode": "planned",
    "memory": DEFAULT_DEV_TEAM["memory"],
    "agents": {
        "memory_worker": {"provider": "ollama", "role": "memory_compression", "can_edit_files": False, "can_run_commands": False},
        "reasoner": {"provider": "ollama", "role": "local_reasoning", "can_edit_files": False, "can_run_commands": False},
    },
    "routing": {"bug_fix": ["reasoner", "memory_worker"], "new_feature": ["reasoner", "memory_worker"], "handoff": ["memory_worker"]},
    "safety": DEFAULT_DEV_TEAM["safety"],
}

REVIEW_HEAVY_TEAM = deepcopy(DEFAULT_DEV_TEAM)
REVIEW_HEAVY_TEAM["team_name"] = "review_heavy"
REVIEW_HEAVY_TEAM["agents"]["reviewer"] = {
    "provider": "openrouter", "role": "design_and_diff_review", "can_edit_files": False, "can_run_commands": False
}
REVIEW_HEAVY_TEAM["routing"]["new_feature"] = ["explorer", "reasoner", "coder", "tester", "reviewer", "memory_worker"]
REVIEW_HEAVY_TEAM["routing"]["bug_fix"] = ["explorer", "coder", "tester", "reviewer", "memory_worker"]

FAST_BUGFIX_TEAM = deepcopy(DEFAULT_DEV_TEAM)
FAST_BUGFIX_TEAM["team_name"] = "fast_bugfix"
FAST_BUGFIX_TEAM["routing"]["bug_fix"] = ["coder", "tester", "memory_worker"]
FAST_BUGFIX_TEAM["routing"]["test_repair"] = ["tester", "memory_worker"]

RESEARCH_THEN_CODE_TEAM = deepcopy(DEFAULT_DEV_TEAM)
RESEARCH_THEN_CODE_TEAM["team_name"] = "research_then_code"
RESEARCH_THEN_CODE_TEAM["routing"]["new_feature"] = ["explorer", "reasoner", "coder", "tester", "memory_worker"]
RESEARCH_THEN_CODE_TEAM["routing"]["large_refactor"] = ["explorer", "reasoner", "coder", "tester", "reasoner", "memory_worker"]

PRESETS = {
    "default_dev_team": DEFAULT_DEV_TEAM,
    "local_only": LOCAL_ONLY_TEAM,
    "review_heavy": REVIEW_HEAVY_TEAM,
    "fast_bugfix": FAST_BUGFIX_TEAM,
    "research_then_code": RESEARCH_THEN_CODE_TEAM,
}


class TeamError(ValueError):
    pass


class TeamManager:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.directory = store.state_dir / "teams"

    def init(self, preset: str = "default_dev_team") -> Path:
        if preset not in PRESETS:
            raise TeamError(f"Unknown team preset: {preset}")
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{preset}.json"
        if not path.exists():
            write_text(path, json.dumps(PRESETS[preset], indent=2) + "\n")
        self.validate(self.load(preset))
        return path

    def list(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(path.stem for path in self.directory.glob("*.json"))

    def load(self, name: str) -> dict[str, Any]:
        path = self.directory / f"{name}.json"
        if not path.exists():
            raise TeamError(f"Team not found: {name}. Run `continuum team init {name}` or choose a listed team.")
        return json.loads(path.read_text(encoding="utf-8"))

    def validate(self, config: dict[str, Any]) -> None:
        agents = config.get("agents", {})
        provider_config_path = self.store.state_dir / "providers.json"
        configured: dict[str, Any] = {}
        if provider_config_path.exists():
            configured = json.loads(provider_config_path.read_text(encoding="utf-8")).get("providers", {})
        for name, agent in agents.items():
            provider = agent.get("provider")
            if provider not in PROVIDERS and provider not in configured:
                raise TeamError(f"Unknown provider for {name}: {provider}")
            if (provider in MODEL_PROVIDERS or configured.get(provider, {}).get("kind") == "model") and agent.get("can_edit_files"):
                raise TeamError(f"Model provider role cannot edit files by default: {name}")
            parent = agent.get("parent")
            if parent and (parent == name or parent not in agents):
                raise TeamError(f"Invalid parent role for {name}: {parent}")
            unknown_delegates = [member for member in agent.get("delegates", []) if member not in agents or member == name]
            if unknown_delegates:
                raise TeamError(f"Invalid delegates for {name}: {', '.join(unknown_delegates)}")
        for route, members in config.get("routing", {}).items():
            missing = [member for member in members if member not in agents]
            if missing:
                raise TeamError(f"Route {route} contains unknown roles: {', '.join(missing)}")

    @staticmethod
    def classify(request: str) -> str:
        lowered = request.lower()
        if any(term in lowered for term in ("refactor", "rewrite", "migration")):
            return "large_refactor"
        if any(term in lowered for term in ("test", "spec", "assertion")) and any(term in lowered for term in ("fail", "fix", "repair")):
            return "test_repair"
        if any(term in lowered for term in ("bug", "fix", "crash", "error", "failure")):
            return "bug_fix"
        if any(term in lowered for term in ("doc", "readme", "documentation")):
            return "documentation"
        return "new_feature"

    def explain(self, team_name: str, request: str, task_type: str | None = None) -> dict[str, Any]:
        config = self.load(team_name)
        self.validate(config)
        selected_type = task_type or self.classify(request)
        route = config.get("routing", {}).get(selected_type) or config.get("routing", {}).get("new_feature", [])
        return {
            "team_name": team_name,
            "task_type": selected_type,
            "request": request,
            "steps": [
                {"order": number, "name": role, **config["agents"][role]} for number, role in enumerate(route, start=1)
            ],
        }

    def plan_tasks(self, team_name: str, request: str, task_type: str | None = None) -> list[dict[str, Any]]:
        plan = self.explain(team_name, request, task_type)
        tasks: list[dict[str, Any]] = []
        for step in plan["steps"]:
            title = f"[{step['name']}] {request}"
            task = self.store.create_task(title, "specialist")
            task = self.store.assign_task(task["task_id"], f"{step['name']}:{step['provider']}")
            tasks.append(task)
        self.store.event(
            "team_workflow_planned",
            {"team": team_name, "task_type": plan["task_type"], "request": request, "tasks": [item["task_id"] for item in tasks]},
        )
        return tasks
