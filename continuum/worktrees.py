"""Git worktree isolation for controlled Continuum tasks."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .core import MemoryStore, compact_text, slug, utc_now, write_text


class WorktreeError(RuntimeError):
    pass


class WorktreeManager:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.root = store.state_dir / "worktrees"
        self.metadata_file = store.state_dir / "worktrees.json"
        self.schedules_file = store.state_dir / "worktree_schedules.json"
        self.context_dir = store.state_dir / "worktree_context"

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.metadata_file.exists():
            return {}
        return json.loads(self.metadata_file.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        write_text(self.metadata_file, json.dumps(data, indent=2) + "\n")

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *args], cwd=str(cwd or self.store.project), capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise WorktreeError((result.stderr or result.stdout).strip() or f"git {' '.join(args)} failed")
        return result.stdout.strip()

    def create(self, task_ref: str) -> dict[str, Any]:
        task = self.store.get_task(task_ref)
        if not task:
            raise WorktreeError(f"Unknown task: {task_ref}")
        if shutil.which("git") is None:
            raise WorktreeError("Git is not installed. Install Git before creating worktrees.")
        metadata = self._read()
        if task_ref.upper() in metadata:
            raise WorktreeError(f"Worktree already exists for {task_ref.upper()}.")
        self._git("-C", str(self.store.project), "rev-parse", "--is-inside-work-tree")
        branch = f"continuum/{task_ref.lower()}"
        path = self.root / task_ref.upper()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._git("-C", str(self.store.project), "worktree", "add", "-b", branch, str(path), "HEAD")
        record = {
            "task_id": task_ref.upper(), "branch": branch, "path": str(path),
            "created_at": utc_now(), "status": "ACTIVE", "test_result": None,
            "completion_note": None, "rollback": f"git branch -D {branch}",
            "review_status": None, "review_note": None,
            "test_sha": None, "review_sha": None,
        }
        metadata[task_ref.upper()] = record
        self._write(metadata)
        self.store.assign_task(task_ref, task.get("agent") or "worktree", branch)
        self.store.event("worktree_created", record)
        return record

    def schedule(
        self,
        objective: str,
        lane_specs: list[str],
        dependency_specs: list[str] | None = None,
        context_mode: str = "compact",
    ) -> dict[str, Any]:
        lanes = self._parse_lanes(lane_specs) if lane_specs else self._infer_lanes()
        if not lanes:
            raise WorktreeError(
                "No safe parallel lanes found. Run `continuum worktree schedule \"<objective>\" "
                "--lane backend:claude:src/api --lane tests:codex:tests`."
            )
        dependencies = self._parse_dependencies(dependency_specs or [], lanes)
        self._reject_file_conflicts(lanes)
        schedules = self._read_schedules()
        schedule_id = f"P{len(schedules) + 1:04d}"
        lane_records: list[dict[str, Any]] = []
        for lane in lanes:
            task = self.store.create_task(f"{objective} [{lane['role']}]", "parallel")
            record = self.create(task["task_id"])
            if lane["paths"]:
                self.store.claim_files(task["task_id"], lane["agent"], lane["paths"])
            record.update(
                {
                    "schedule_id": schedule_id,
                    "objective": compact_text(objective, 400),
                    "role": lane["role"],
                    "agent": lane["agent"],
                    "owned_paths": lane["paths"],
                    "dependencies": dependencies.get(lane["role"], []),
                    "context_mode": context_mode,
                }
            )
            self._update_record(task["task_id"], record)
            context_path = self._write_lane_context(schedule_id, record)
            record["context_path"] = str(context_path)
            self._update_record(task["task_id"], record)
            lane_records.append(record)
        schedule = {
            "schedule_id": schedule_id,
            "created_at": utc_now(),
            "objective": compact_text(objective, 800),
            "status": "PLANNED",
            "context_mode": context_mode,
            "lanes": lane_records,
        }
        schedules[schedule_id] = schedule
        self._write_schedules(schedules)
        self.store.event(
            "worktree_schedule_created",
            {
                "schedule_id": schedule_id,
                "summary": f"Scheduled {len(lane_records)} parallel worktree lane(s).",
                "objective": compact_text(objective, 400),
            },
        )
        return schedule

    def schedules(self) -> list[dict[str, Any]]:
        return list(self._read_schedules().values())

    def schedule_status(self, schedule_ref: str) -> dict[str, Any]:
        schedule = self._require_schedule(schedule_ref)
        refreshed = []
        for lane in schedule["lanes"]:
            record = self._read().get(lane["task_id"], lane)
            task = self.store.get_task(lane["task_id"])
            record["task_status"] = task["status"] if task else "UNKNOWN"
            record["merge_ready"] = (
                record.get("test_result") == "PASS"
                and record.get("review_status") == "APPROVED"
                and record.get("status") == "ACTIVE"
                and not self._dependency_blockers(record)
            )
            refreshed.append(record)
        schedule["lanes"] = refreshed
        if all(lane.get("status") == "MERGED" for lane in refreshed):
            schedule["status"] = "MERGED"
        elif any(lane.get("task_status") == "FAILED" for lane in refreshed):
            schedule["status"] = "FAILED"
        else:
            schedule["status"] = "ACTIVE"
        schedules = self._read_schedules()
        schedules[schedule["schedule_id"]] = schedule
        self._write_schedules(schedules)
        return schedule

    def list(self) -> list[dict[str, Any]]:
        return list(self._read().values())

    def diff(self, task_ref: str) -> dict[str, Any]:
        record = self._require(task_ref)
        path = Path(record["path"])
        base = self._git("-C", str(self.store.project), "merge-base", "HEAD", record["branch"])
        names = self._git("-C", str(path), "diff", "--name-only", base, record["branch"]).splitlines()
        local_names = self._git("-C", str(path), "diff", "--name-only", "HEAD").splitlines()
        summary = self._git("-C", str(path), "diff", "--stat", base, record["branch"])
        if local_names:
            summary = (summary + "\nUncommitted:\n" + self._git("-C", str(path), "diff", "--stat", "HEAD")).strip()
        names.extend(local_names)
        record.update({"changed_files": sorted(set(item for item in names if item)), "diff_summary": summary})
        metadata = self._read()
        metadata[task_ref.upper()] = record
        self._write(metadata)
        return record

    def record_tests(self, task_ref: str, passed: bool, note: str) -> dict[str, Any]:
        metadata = self._read()
        record = self._require(task_ref)
        record["test_result"] = "PASS" if passed else "FAIL"
        record["completion_note"] = note
        record["test_sha"] = self._git("-C", str(record["path"]), "rev-parse", "HEAD")
        metadata[task_ref.upper()] = record
        self._write(metadata)
        self.store.event("worktree_tests", {"task_id": task_ref.upper(), "result": record["test_result"], "summary": note})
        return record

    def record_review(self, task_ref: str, approved: bool, note: str) -> dict[str, Any]:
        metadata = self._read()
        record = self._require(task_ref)
        record["review_status"] = "APPROVED" if approved else "CHANGES_REQUESTED"
        record["review_note"] = note
        record["review_sha"] = self._git("-C", str(record["path"]), "rev-parse", "HEAD")
        metadata[task_ref.upper()] = record
        self._write(metadata)
        self.store.event("worktree_review", {"task_id": task_ref.upper(), "result": record["review_status"], "summary": note})
        return record

    def merge(self, task_ref: str) -> dict[str, Any]:
        metadata = self._read()
        record = self._require(task_ref)
        blockers = self._dependency_blockers(record)
        if blockers:
            raise WorktreeError(
                f"Cannot merge {task_ref.upper()} before dependency lane(s) are merged: {', '.join(blockers)}."
            )
        if record.get("test_result") != "PASS":
            raise WorktreeError(
                f"Cannot merge {task_ref.upper()} before passing tests. Run `continuum worktree test-result {task_ref.upper()} --pass --note \"<command/result>\"`."
            )
        if record.get("review_status") != "APPROVED":
            raise WorktreeError(
                f"Cannot merge {task_ref.upper()} before review approval. Run `continuum worktree review {task_ref.upper()} --approve --note \"<review>\"`."
            )
        head_sha = self._git("-C", str(record["path"]), "rev-parse", "HEAD")
        if record.get("test_sha") != head_sha or record.get("review_sha") != head_sha:
            raise WorktreeError(
                f"Cannot merge {task_ref.upper()}: the worktree changed after its test or review gate. Record both gates again for commit {head_sha}."
            )
        if self._git("-C", str(record["path"]), "status", "--porcelain"):
            raise WorktreeError(f"Cannot merge {task_ref.upper()}: the isolated worktree has uncommitted changes.")
        if self._git("-C", str(self.store.project), "status", "--porcelain"):
            raise WorktreeError("Main working tree is dirty. Commit or stash it before merging a task worktree.")
        self._git("-C", str(self.store.project), "merge", "--no-ff", record["branch"], "-m", f"Merge {task_ref.upper()} worktree")
        record["status"] = "MERGED"
        metadata[task_ref.upper()] = record
        self._write(metadata)
        self.store.set_task_status(task_ref, "DONE", record.get("completion_note") or "Merged worktree.")
        self.store.event("worktree_merged", {"task_id": task_ref.upper(), "branch": record["branch"]})
        return record

    def discard(self, task_ref: str, force: bool = False) -> dict[str, Any]:
        metadata = self._read()
        record = self._require(task_ref)
        args = ["-C", str(self.store.project), "worktree", "remove", record["path"]]
        if force:
            args.append("--force")
        self._git(*args)
        self._git("-C", str(self.store.project), "branch", "-D", record["branch"])
        record["status"] = "DISCARDED"
        metadata[task_ref.upper()] = record
        self._write(metadata)
        self.store.event("worktree_discarded", {"task_id": task_ref.upper(), "branch": record["branch"]})
        return record

    def _require(self, task_ref: str) -> dict[str, Any]:
        value = self._read().get(task_ref.upper())
        if not value:
            raise WorktreeError(f"No worktree exists for {task_ref.upper()}. Run `continuum worktree create {task_ref.upper()}`.")
        return value

    def _update_record(self, task_ref: str, record: dict[str, Any]) -> None:
        metadata = self._read()
        metadata[task_ref.upper()] = record
        self._write(metadata)

    def _read_schedules(self) -> dict[str, dict[str, Any]]:
        if not self.schedules_file.exists():
            return {}
        return json.loads(self.schedules_file.read_text(encoding="utf-8"))

    def _write_schedules(self, data: dict[str, dict[str, Any]]) -> None:
        write_text(self.schedules_file, json.dumps(data, indent=2) + "\n")

    def _require_schedule(self, schedule_ref: str) -> dict[str, Any]:
        normalized = schedule_ref.strip().upper()
        if not normalized.startswith("P"):
            normalized = f"P{int(normalized):04d}" if normalized.isdigit() else normalized
        schedule = self._read_schedules().get(normalized)
        if not schedule:
            raise WorktreeError(f"Unknown worktree schedule: {schedule_ref}")
        return schedule

    def _parse_lanes(self, values: list[str]) -> list[dict[str, Any]]:
        lanes = []
        seen_roles: set[str] = set()
        for value in values:
            parts = value.split(":", 2)
            if len(parts) != 3:
                raise WorktreeError(
                    f"Invalid lane `{value}`. Use role:agent:path1,path2, for example `backend:claude:src/api,src/db`."
                )
            role, agent, raw_paths = (part.strip() for part in parts)
            role = slug(role).lower()
            if role in seen_roles:
                raise WorktreeError(f"Duplicate lane role: {role}")
            if agent not in {"claude", "codex", "gemini"}:
                raise WorktreeError(f"Invalid lane agent `{agent}`. Use claude, codex or gemini.")
            paths = [self._normalize_path(item) for item in raw_paths.split(",") if item.strip()]
            if not paths:
                raise WorktreeError(f"Lane `{role}` must own at least one path.")
            lanes.append({"role": role, "agent": agent, "paths": paths})
            seen_roles.add(role)
        return lanes

    def _infer_lanes(self) -> list[dict[str, Any]]:
        candidates = [
            ("backend", "claude", ["backend", "server", "api", "src/api", "src/server"]),
            ("ui", "gemini", ["frontend", "ui", "web", "app", "src/ui", "src/components"]),
            ("tests", "codex", ["tests", "test", "__tests__"]),
            ("docs", "gemini", ["docs", "README.md"]),
        ]
        lanes = []
        claimed: set[str] = set()
        for role, agent, paths in candidates:
            existing = [self._normalize_path(path) for path in paths if (self.store.project / path).exists()]
            existing = [path for path in existing if path not in claimed]
            if existing:
                lanes.append({"role": role, "agent": agent, "paths": existing})
                claimed.update(existing)
        return lanes

    def _parse_dependencies(self, values: list[str], lanes: list[dict[str, Any]]) -> dict[str, list[str]]:
        roles = {lane["role"] for lane in lanes}
        result: dict[str, list[str]] = {role: [] for role in roles}
        for value in values:
            parts = value.split(":", 1)
            if len(parts) != 2:
                raise WorktreeError(f"Invalid dependency `{value}`. Use role:depends_on_role.")
            role, depends_on = (slug(part).lower() for part in parts)
            if role not in roles or depends_on not in roles:
                raise WorktreeError(f"Dependency `{value}` references an unknown lane.")
            if role == depends_on:
                raise WorktreeError(f"Dependency `{value}` points to itself.")
            result[role].append(depends_on)
        return result

    def _reject_file_conflicts(self, lanes: list[dict[str, Any]]) -> None:
        owners: list[tuple[str, str]] = []
        for lane in lanes:
            for path in lane["paths"]:
                for prior_path, prior_role in owners:
                    if path == prior_path or path.startswith(prior_path + "/") or prior_path.startswith(path + "/"):
                        raise WorktreeError(
                            f"Parallel lane conflict: `{path}` in `{lane['role']}` overlaps `{prior_path}` in `{prior_role}`."
                        )
                owners.append((path, lane["role"]))

    @staticmethod
    def _normalize_path(value: str) -> str:
        normalized = Path(value.strip()).as_posix()
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized in {"", "."} or normalized.startswith("../") or normalized == "..":
            raise WorktreeError(f"Unsafe lane path: {value}")
        if re.match(r"^[A-Za-z]:", normalized):
            raise WorktreeError(f"Lane paths must be project-relative: {value}")
        return normalized.rstrip("/")

    def _write_lane_context(self, schedule_id: str, record: dict[str, Any]) -> Path:
        packet = self.store.context_packet(record["role"], record["objective"], record.get("context_mode", "compact"))
        path = self.context_dir / schedule_id / f"{record['task_id']}.md"
        owned = "\n".join(f"- `{item}`" for item in record.get("owned_paths", [])) or "- No owned paths."
        deps = ", ".join(record.get("dependencies", [])) or "none"
        text = f"""# Parallel Worktree Context

Schedule: `{schedule_id}`
Task: `{record['task_id']}`
Role: `{record['role']}`
Agent: `{record['agent']}`
Branch: `{record['branch']}`
Worktree: `{record['path']}`
Dependencies: {deps}
Estimated context: {packet['estimated_tokens']} tokens

## Objective
{record['objective']}

## Owned Paths
{owned}

## Rules
- Edit only the owned paths for this lane.
- Read other files when needed, but do not change another lane's owned paths.
- Before merge, record tests with `continuum worktree test-result {record['task_id']} --pass --note "<command/result>"`.
- Before merge, record review with `continuum worktree review {record['task_id']} --approve --note "<review>"`.
- Merge only after dependencies are done and both gates are recorded.

## Resume Command
```bash
continuum worktree resume {record['task_id']} {record['agent']} {record.get('context_mode', 'compact')}
```

## Shared Context
{packet['text']}
"""
        write_text(path, compact_text(text, 8_000))
        return path

    def _dependency_blockers(self, record: dict[str, Any]) -> list[str]:
        dependencies = record.get("dependencies", [])
        schedule_id = record.get("schedule_id")
        if not dependencies or not schedule_id:
            return []
        schedule = self._read_schedules().get(schedule_id)
        if not schedule:
            return list(dependencies)
        records = self._read()
        blockers = []
        for dependency in dependencies:
            lane = next((item for item in schedule["lanes"] if item.get("role") == dependency), None)
            if not lane:
                blockers.append(dependency)
                continue
            latest = records.get(lane["task_id"], lane)
            task = self.store.get_task(lane["task_id"])
            if latest.get("status") != "MERGED" and (not task or task.get("status") != "DONE"):
                blockers.append(dependency)
        return blockers
