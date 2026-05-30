"""One-command objective planning: compose route + worktree primitives.

`continuum objective "<goal>"` turns a single objective into a coordinated,
controlled multi-agent plan by composing existing Continuum primitives:

- the route classifier (`TeamManager.classify` / `TeamManager.explain`) infers
  the task type and the team route;
- lanes are derived from the routed roles (or supplied explicitly) and mapped to
  the agent CLIs Continuum drives;
- `--mode schedule` reuses `WorktreeManager.schedule`, which already enforces
  overlapping-lane rejection, dependency edges, file ownership and bounded
  context packets -- this module never re-implements that logic.

This module owns no state machine of its own. It is a thin planner that calls
the existing managers and shapes a deterministic result for the CLI to render.
"""

from __future__ import annotations

from typing import Any

from .core import MemoryStore, slug
from .teams import PRESETS, TeamError, TeamManager
from .worktrees import WorktreeError, WorktreeManager

# Worktree lanes run real agent CLIs; only these three are drivable as lanes.
AGENT_PROVIDERS = ("claude", "codex", "gemini")

# Map a team's configured provider to the lane agent CLI that drives it.
PROVIDER_TO_AGENT = {
    "claude_code": "claude",
    "gemini_cli": "gemini",
    "codex": "codex",
}

REVIEW_POLICIES = ("all", "risky", "none")
MODES = ("plan", "schedule")


class ObjectiveError(ValueError):
    """Raised when an objective plan cannot be assembled safely."""


def _parse_kv(values: list[str], label: str) -> dict[str, str]:
    """Parse repeatable `role=value` (or `role:value`) flags into a role->value map."""
    result: dict[str, str] = {}
    for raw in values:
        if "=" in raw:
            role, value = raw.split("=", 1)
        elif ":" in raw:
            role, value = raw.split(":", 1)
        else:
            raise ObjectiveError(f"Invalid {label} `{raw}`. Use role={label} or role:{label}, for example `backend=claude`.")
        role = slug(role).lower()
        value = value.strip()
        if not role or not value:
            raise ObjectiveError(f"Invalid {label} `{raw}`. Both role and value are required.")
        if role in result:
            raise ObjectiveError(f"Duplicate {label} for role `{role}`.")
        result[role] = value
    return result


def _derive_lanes_from_route(plan: dict[str, Any]) -> list[dict[str, str]]:
    """Derive role->agent lanes from a routed team plan.

    Only roles whose provider maps to a drivable agent CLI become lanes; model
    roles (e.g. reasoner/memory_worker on ollama/openrouter) are recorded as
    advisory steps but cannot own a worktree.
    """
    lanes: list[dict[str, str]] = []
    seen: set[str] = set()
    for step in plan["steps"]:
        agent = PROVIDER_TO_AGENT.get(step["provider"])
        if agent is None:
            continue
        role = slug(step["name"]).lower()
        if role in seen:
            continue
        lanes.append({"role": role, "agent": agent})
        seen.add(role)
    return lanes


def plan_objective(
    store: MemoryStore,
    goal: str,
    *,
    team: str = "default_dev_team",
    task_type: str | None = None,
    agent_specs: list[str] | None = None,
    path_specs: list[str] | None = None,
    depends_on: list[str] | None = None,
    tests: str | None = None,
    review_policy: str = "all",
    mode: str = "plan",
    context_mode: str = "compact",
) -> dict[str, Any]:
    """Compose a coordinated objective plan.

    In ``plan`` mode this creates one routed task per lane and emits context for
    the user to drive manually. In ``schedule`` mode it additionally calls
    ``WorktreeManager.schedule`` to create branches/worktrees/context packets for
    lanes that own explicit paths.
    """
    goal = goal.strip()
    if not goal:
        raise ObjectiveError("Objective goal is empty. Example: `continuum objective \"Add billing\"`.")
    if review_policy not in REVIEW_POLICIES:
        raise ObjectiveError(f"Invalid review policy: {review_policy}. Use one of {', '.join(REVIEW_POLICIES)}.")
    if mode not in MODES:
        raise ObjectiveError(f"Invalid mode: {mode}. Use plan or schedule.")

    teams = TeamManager(store)
    # Reuse the route classifier as the single source of inference. team init is
    # idempotent, so this also bootstraps a built-in preset on a fresh project.
    if team in PRESETS and team not in teams.list():
        try:
            teams.init(team)
        except TeamError:
            pass
    try:
        route = teams.explain(team, goal, task_type)
    except TeamError as error:
        raise ObjectiveError(str(error)) from error
    inferred_type = route["task_type"]

    explicit_agents = _parse_kv(agent_specs or [], "agent")
    if explicit_agents:
        lanes = [{"role": role, "agent": agent} for role, agent in explicit_agents.items()]
    else:
        lanes = _derive_lanes_from_route(route)
    if not lanes:
        raise ObjectiveError(
            "No drivable lanes could be derived from the route. Provide explicit lanes with "
            "`--agent role=agent` (agent in claude, codex, gemini)."
        )
    for lane in lanes:
        if lane["agent"] not in AGENT_PROVIDERS:
            raise ObjectiveError(
                f"Unknown agent `{lane['agent']}` for lane `{lane['role']}`. Use claude, codex or gemini."
            )

    lane_roles = {lane["role"] for lane in lanes}
    paths = _parse_kv(path_specs or [], "path")
    for role in paths:
        if role not in lane_roles:
            raise ObjectiveError(f"--path references unknown lane `{role}`. Known lanes: {', '.join(sorted(lane_roles))}.")

    # Validate dependency references up front so plan mode fails as loudly as
    # schedule mode would (WorktreeManager re-validates these in schedule mode).
    dependencies = _parse_dependencies(depends_on or [], lane_roles)

    for lane in lanes:
        lane_paths = [item.strip() for item in paths.get(lane["role"], "").split(",") if item.strip()]
        lane["paths"] = lane_paths
        lane["depends_on"] = dependencies.get(lane["role"], [])

    timeline = _order_lanes(lanes, dependencies)

    result: dict[str, Any] = {
        "goal": goal,
        "team": team,
        "task_type": inferred_type,
        "review_policy": review_policy,
        "tests": tests,
        "mode": mode,
        "context_mode": context_mode,
        "lanes": lanes,
        "timeline": [lane["role"] for lane in timeline],
        "scheduled": False,
        "schedule_id": None,
        "tasks": [],
        "notes": [],
    }

    lanes_with_paths = [lane for lane in lanes if lane["paths"]]
    lanes_without_paths = [lane for lane in lanes if not lane["paths"]]

    if mode == "schedule" and lanes_with_paths:
        manager = WorktreeManager(store)
        lane_specs = [f"{lane['role']}:{lane['agent']}:{','.join(lane['paths'])}" for lane in lanes_with_paths]
        dependency_specs = [
            f"{lane['role']}:{dep}"
            for lane in lanes_with_paths
            for dep in lane["depends_on"]
        ]
        try:
            schedule = manager.schedule(goal, lane_specs, dependency_specs, context_mode)
        except WorktreeError as error:
            raise ObjectiveError(str(error)) from error
        result["scheduled"] = True
        result["schedule_id"] = schedule["schedule_id"]
        by_role = {lane["role"]: lane for lane in schedule["lanes"]}
        for lane in lanes:
            scheduled = by_role.get(lane["role"])
            if scheduled:
                lane.update(
                    {
                        "task_id": scheduled["task_id"],
                        "branch": scheduled["branch"],
                        "worktree": scheduled["path"],
                        "context_path": scheduled["context_path"],
                    }
                )
                result["tasks"].append(scheduled["task_id"])
        if lanes_without_paths:
            roles = ", ".join(lane["role"] for lane in lanes_without_paths)
            result["notes"].append(
                f"No worktree created for lane(s) without --path: {roles}. Tasks were created so they can be driven manually."
            )
        # Plan-only tasks for the path-less lanes keep one task per lane.
        for lane in lanes_without_paths:
            lane["task_id"] = _create_plan_task(store, goal, lane["role"], lane["agent"])
            result["tasks"].append(lane["task_id"])
    else:
        if mode == "schedule" and not lanes_with_paths:
            result["notes"].append(
                "Schedule requested but no lane has --path; created plan tasks only (no worktrees). "
                "Add `--path role=glob,...` to isolate lanes in worktrees."
            )
        for lane in lanes:
            lane["task_id"] = _create_plan_task(store, goal, lane["role"], lane["agent"])
            result["tasks"].append(lane["task_id"])

    result["next_commands"] = _next_commands(result)
    store.event(
        "objective_planned",
        {
            "summary": f"Planned objective '{goal}' as {inferred_type} across {len(lanes)} lane(s).",
            "task_type": inferred_type,
            "mode": mode,
            "scheduled": result["scheduled"],
            "schedule_id": result["schedule_id"],
            "lanes": len(lanes),
        },
    )
    return result


def _create_plan_task(store: MemoryStore, goal: str, role: str, agent: str) -> str:
    task = store.create_task(f"{goal} [{role}]", "parallel")
    task = store.assign_task(task["task_id"], agent)
    return task["task_id"]


def _parse_dependencies(values: list[str], roles: set[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for raw in values:
        if ":" not in raw:
            raise ObjectiveError(f"Invalid dependency `{raw}`. Use role:depends_on_role.")
        role, depends_on = (slug(part).lower() for part in raw.split(":", 1))
        if role not in roles or depends_on not in roles:
            raise ObjectiveError(f"Dependency `{raw}` references an unknown lane. Known lanes: {', '.join(sorted(roles))}.")
        if role == depends_on:
            raise ObjectiveError(f"Dependency `{raw}` points to itself.")
        result.setdefault(role, []).append(depends_on)
    return result


def _order_lanes(lanes: list[dict[str, Any]], dependencies: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Topologically order lanes so dependencies run first (stable for cycles)."""
    by_role = {lane["role"]: lane for lane in lanes}
    ordered: list[dict[str, Any]] = []
    placed: set[str] = set()

    def visit(role: str, trail: set[str]) -> None:
        if role in placed or role in trail or role not in by_role:
            return
        trail = trail | {role}
        for dep in dependencies.get(role, []):
            visit(dep, trail)
        if role not in placed:
            placed.add(role)
            ordered.append(by_role[role])

    for lane in lanes:
        visit(lane["role"], set())
    return ordered


def _next_commands(result: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    context_mode = result["context_mode"]
    review_policy = result["review_policy"]
    timeline_roles = result["timeline"]
    by_role = {lane["role"]: lane for lane in result["lanes"]}
    for role in timeline_roles:
        lane = by_role[role]
        task_id = lane.get("task_id")
        if not task_id:
            continue
        if result["scheduled"] and lane.get("worktree"):
            commands.append(f"continuum worktree resume {task_id} {lane['agent']} {context_mode}")
            tests = result.get("tests")
            note = tests or "<command/result>"
            commands.append(f"continuum worktree test-result {task_id} --pass --note \"{note}\"")
            if review_policy != "none":
                commands.append(f"continuum worktree review {task_id} --approve --note \"<review>\"")
            commands.append(f"continuum worktree merge {task_id}")
        else:
            commands.append(f"continuum resume {lane['agent']} {context_mode}  # drive {role} task {task_id}")
        commands.append(f"continuum pr-packet {task_id}")
    return commands
