"""Git worktree isolation for controlled Continuum tasks."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .core import MemoryStore, utc_now, write_text


class WorktreeError(RuntimeError):
    pass


class WorktreeManager:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.root = store.state_dir / "worktrees"
        self.metadata_file = store.state_dir / "worktrees.json"

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
        branch = f"continuum/{task_ref.upper().lower()}"
        path = self.root / task_ref.upper()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._git("-C", str(self.store.project), "worktree", "add", "-b", branch, str(path), "HEAD")
        record = {
            "task_id": task_ref.upper(), "branch": branch, "path": str(path),
            "created_at": utc_now(), "status": "ACTIVE", "test_result": None,
            "completion_note": None, "rollback": f"git branch -D {branch}",
            "review_status": None, "review_note": None,
        }
        metadata[task_ref.upper()] = record
        self._write(metadata)
        self.store.assign_task(task_ref, task.get("agent") or "worktree", branch)
        self.store.event("worktree_created", record)
        return record

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
        metadata[task_ref.upper()] = record
        self._write(metadata)
        self.store.event("worktree_tests", {"task_id": task_ref.upper(), "result": record["test_result"], "summary": note})
        return record

    def record_review(self, task_ref: str, approved: bool, note: str) -> dict[str, Any]:
        metadata = self._read()
        record = self._require(task_ref)
        record["review_status"] = "APPROVED" if approved else "CHANGES_REQUESTED"
        record["review_note"] = note
        metadata[task_ref.upper()] = record
        self._write(metadata)
        self.store.event("worktree_review", {"task_id": task_ref.upper(), "result": record["review_status"], "summary": note})
        return record

    def merge(self, task_ref: str) -> dict[str, Any]:
        metadata = self._read()
        record = self._require(task_ref)
        if record.get("test_result") != "PASS":
            raise WorktreeError(
                f"Cannot merge {task_ref.upper()} before passing tests. Run `continuum worktree test-result {task_ref.upper()} --pass --note \"<command/result>\"`."
            )
        if record.get("review_status") != "APPROVED":
            raise WorktreeError(
                f"Cannot merge {task_ref.upper()} before review approval. Run `continuum worktree review {task_ref.upper()} --approve --note \"<review>\"`."
            )
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
