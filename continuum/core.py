"""Local persistence and compact Markdown handoffs for Continuum."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_CONTEXT_LIMIT = 100_000
DEFAULT_THRESHOLD = 0.80
CONTEXT_BUDGETS = {
    "startup": 800,
    "handoff": 1_200,
    "retrieval_default": 2_000,
    "retrieval_max": 6_000,
    "raw_log_default": 1_000,
    "compact": 800,
    "normal": 2_000,
    "deep": 6_000,
}
TASK_STATUSES = {"CREATED", "ASSIGNED", "RUNNING", "CHECKPOINTED", "REVIEWING", "TESTING", "DONE", "BLOCKED", "FAILED", "NEEDS_USER"}
FINAL_TASK_STATUSES = {"DONE", "FAILED"}
WORKFLOW_STATUSES = {"PLANNED", "RUNNING", "DONE", "BLOCKED", "FAILED"}
NON_WRITING_PROVIDERS = {"ollama", "openrouter"}
MAX_FIELD_CHARS = 1_200
MAX_CHANGE_LINES = 40
MAX_SESSION_EXCERPT_LINES = 30
IGNORE_DIRS = {
    ".git",
    ".continuum",
    ".ai_memory",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "node_modules",
    "dist",
    "build",
}
SYNTHETIC_TASK_PATTERNS = (
    re.compile(r"^Wrapped `[^`]+` session `[^`]+` completed\.$"),
    re.compile(r"^Interactive `[^`]+` session `[^`]+` completed\.$"),
    re.compile(r"^`[^`]+` reached its estimated context checkpoint\.$"),
    re.compile(r"^`[^`]+` reached its estimated context checkpoint in an interactive terminal\.$"),
)

AGENT_MEMORY_BLOCK = """## Continuum Shared Memory

Before continuing prior work, read:

- `.continuum/current.md`

Use Continuum MCP for targeted retrieval: read current state or latest handoff
only when needed, search exact topics, then expand specific memory IDs. Do not
load full historical logs by default.

Use the existing handoff and decisions. Before stopping substantive work,
write a Continuum handoff with the exact next action.

Keep status output compact: facts, actions and blockers; no filler.
"""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return cleaned or "project"


def compact_text(value: str, limit: int = MAX_FIELD_CHARS) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 24].rstrip() + "\n...[content truncated]"


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def project_key(path: Path) -> str:
    digest = hashlib.sha1(str(path.resolve()).lower().encode("utf-8")).hexdigest()[:8]
    return f"{slug(path.name)}-{digest}"


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def append_memory_block(path: Path, heading: str) -> bool:
    if path.exists():
        content = path.read_text(encoding="utf-8")
        if "## Continuum Shared Memory" in content:
            return False
        if content and not content.endswith("\n"):
            content += "\n"
        write_text(path, content + "\n" + AGENT_MEMORY_BLOCK)
        return True
    write_text(path, f"# {heading}\n\n{AGENT_MEMORY_BLOCK}")
    return True


class MemoryStore:
    """State for one project, with compact optional Obsidian mirror notes."""

    def __init__(self, project: Path, vault_dir: Path | None = None) -> None:
        self.project = project.expanduser().resolve()
        self.state_dir = self.project / ".continuum"
        self.config_file = self.state_dir / "config.json"
        configured = self.read_config()
        configured_vault = configured.get("vault_dir")
        self.vault_dir = (
            vault_dir.expanduser().resolve()
            if vault_dir
            else Path(configured_vault).expanduser().resolve()
            if configured_vault
            else None
        )
        self.project_id = project_key(self.project)
        self.notes_dir = self.vault_dir / "Projects" / self.project_id if self.vault_dir else None
        self.index_file = self.vault_dir / "Memory Index.md" if self.vault_dir else None
        self.db_file = self.state_dir / "events.sqlite3"
        self.jsonl_file = self.state_dir / "events.jsonl"

    def read_config(self) -> dict[str, Any]:
        if not self.config_file.exists():
            return {}
        try:
            return json.loads(self.config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def initialize(self, context_limit: int, threshold: float) -> None:
        self.project.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(exist_ok=True)
        (self.state_dir / "session_logs").mkdir(exist_ok=True)
        # Keep machine-local state (sqlite, worktrees, session logs) out of Git so it
        # never dirties the project tree or registers worktrees as embedded repositories.
        gitignore = self.state_dir / ".gitignore"
        if not gitignore.exists():
            write_text(gitignore, "*\n")
        config = {
            "project": str(self.project),
            "project_id": self.project_id,
            "vault_dir": str(self.vault_dir) if self.vault_dir else None,
            "context_limit": context_limit,
            "checkpoint_threshold": threshold,
            "updated_at": utc_now(),
        }
        write_text(self.config_file, json.dumps(config, indent=2) + "\n")
        self.connect().close()
        if self.notes_dir:
            (self.notes_dir / "Sessions").mkdir(parents=True, exist_ok=True)
            self.write_metadata_and_index()
        append_memory_block(self.project / "AGENTS.md", "Agent Instructions")
        append_memory_block(self.project / "CLAUDE.md", "Claude Instructions")
        append_memory_block(self.project / "GEMINI.md", "Gemini Instructions")
        for name, initial in {
            "IMPLEMENTED.md": "# Implemented\n\n",
            "DECISIONS.md": "# Decisions\n\n",
            "TASKS.md": "# Tasks\n\n",
        }.items():
            path = self.project / name
            if not path.exists():
                write_text(path, initial)
        self.event("initialized", {"notes": str(self.notes_dir) if self.notes_dir else None})
        self.write_handoff(
            "Continuum initialized. No active coding task has been recorded.",
            "Record a task with `continuum handoff` or start an agent with `continuum run`.",
        )

    def connect(self) -> sqlite3.Connection:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_file)
        connection.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                title TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                agent TEXT,
                branch TEXT,
                summary TEXT
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS file_locks (
                path TEXT PRIMARY KEY,
                task_id INTEGER NOT NULL,
                agent TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS embeddings (
                memory_key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                vector TEXT NOT NULL,
                source_preview TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS workflows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                team TEXT NOT NULL,
                request TEXT NOT NULL,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL,
                current_step INTEGER NOT NULL DEFAULT 0,
                summary TEXT
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS workflow_steps (
                workflow_id INTEGER NOT NULL,
                step_number INTEGER NOT NULL,
                role TEXT NOT NULL,
                provider TEXT NOT NULL,
                task_id INTEGER,
                status TEXT NOT NULL,
                output TEXT,
                PRIMARY KEY(workflow_id, step_number),
                FOREIGN KEY(workflow_id) REFERENCES workflows(id),
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                workflow_id INTEGER,
                task_id INTEGER,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                kind TEXT NOT NULL,
                body TEXT NOT NULL,
                token_estimate INTEGER NOT NULL,
                FOREIGN KEY(workflow_id) REFERENCES workflows(id),
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS context_graphs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                graph_json TEXT NOT NULL,
                packet_markdown TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS external_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                pid INTEGER NOT NULL,
                process_created_at REAL NOT NULL,
                agent TEXT NOT NULL,
                status TEXT NOT NULL,
                cwd TEXT,
                command_line TEXT NOT NULL,
                integration TEXT NOT NULL,
                context_mode TEXT,
                context_path TEXT,
                UNIQUE(pid, process_created_at)
            )"""
        )
        connection.commit()
        return connection

    @staticmethod
    def delegation_ref(graph_id: int) -> str:
        return f"D{graph_id:04d}"

    @staticmethod
    def parse_delegation_ref(value: str) -> int:
        normalized = value.strip().upper()
        if normalized.startswith("D"):
            normalized = normalized[1:]
        if not normalized.isdigit():
            raise ValueError(f"Invalid delegation id: {value}")
        return int(normalized)

    def create_delegation(
        self,
        planner: str,
        executor: str,
        goal: str,
        *,
        mode: str = "direct",
        budget: str = "normal",
        scope: list[str] | None = None,
        review: str = "on-failure",
        tests: str = "required",
        handoff: str = "strict",
    ) -> dict[str, Any]:
        if mode not in {"direct", "checkpoint", "supervisor"}:
            raise ValueError(f"Invalid delegation mode: {mode}")
        if budget not in {"low", "normal", "high"}:
            raise ValueError(f"Invalid delegation budget: {budget}")
        if review not in {"on-failure", "each-step", "final"}:
            raise ValueError(f"Invalid review policy: {review}")
        if tests not in {"required", "best-effort", "none"}:
            raise ValueError(f"Invalid test policy: {tests}")
        if handoff not in {"strict", "normal"}:
            raise ValueError(f"Invalid handoff policy: {handoff}")

        normalized_scope = []
        for item in scope or []:
            value = str(Path(item).as_posix())
            while value.startswith("./"):
                value = value[2:]
            if value:
                normalized_scope.append(value)
        normalized_scope = sorted(set(normalized_scope))
        task = self.create_task(f"Delegated execution: {goal}", "hierarchical")
        task_id = task["task_id"]
        self.assign_task(task_id, executor)

        context = self.resume_context("compact") if (self.state_dir / "current.md").exists() else ""
        decisions = [
            compact_text(json.dumps(item["payload"], ensure_ascii=True), 240)
            for item in self.recent_events(80)
            if item["kind"] in {"decision", "delegation_decision", "workflow_created", "handoff"}
        ][-5:]
        allowed = ", ".join(f"`{item}`" for item in normalized_scope) if normalized_scope else "No files pre-approved. Inspect first, then claim explicit files before editing."
        inspect = "Project files relevant to the goal; retrieve extra memory by exact query before opening raw logs."
        packet = f"""# Hierarchical Execution Packet

Task ID: `{task_id}`
Role: `{executor}`
Planner: `{planner}`
Delegation mode: `{mode}`
Budget: `{budget}`

## Goal
{compact_text(goal, 600)}

## Reason This Task Exists
The planner role is preserving architecture intent and constraints while the executor role performs one bounded implementation slice.

## Files Allowed To Edit
{allowed}

## Files Only Allowed To Inspect
{inspect}

## Relevant Symbols
- Not inferred automatically. Executor must identify symbols only inside the allowed scope or escalate.

## Constraints
- Follow the existing architecture and recorded Continuum decisions.
- Do not redesign unrelated systems.
- Keep context compact; retrieve targeted memory instead of asking for all history.
- Model providers may reason, but only agent providers may claim or edit files.

## Do Not Change
- Files outside the allowed edit scope without escalating back to `{planner}`.
- Public behavior unrelated to the goal.
- Existing release/version history except through an explicit release task.

## Expected Output
- Patch summary.
- Files changed.
- Tests or checks run.
- Remaining risks.
- Exact next step if incomplete.

## Acceptance Checks
- Test policy: `{tests}`.
- Handoff policy: `{handoff}`.
- No file edits outside the packet scope.
- Any new risk is recorded as a Continuum message or handoff.

## Failure Conditions
- Scope expansion is required.
- Tests fail repeatedly.
- Existing architecture decision conflicts with the implementation.
- The executor cannot identify the relevant files safely.

## When To Escalate Back To Big Model
- Review policy: `{review}`.
- Escalate before changing files outside the allowed list.
- Escalate if a schema, workflow, provider boundary or safety rule must change.

## Context Summary
{compact_text(context, 1_200) or "No compact current state exists yet."}

## Previous Decisions
{chr(10).join(f"- {item}" for item in decisions) or "- No previous decisions retrieved for this packet."}
"""
        graph = {
            "version": 1,
            "kind": "hierarchical_model_delegation",
            "status": "PLANNED",
            "planner": planner,
            "executor": executor,
            "mode": mode,
            "budget": budget,
            "review": review,
            "tests": tests,
            "handoff": handoff,
            "nodes": [
                {"id": "objective", "type": "objective", "title": compact_text(goal, 240)},
                {"id": task_id, "type": "task", "title": compact_text(goal, 240), "status": "ASSIGNED"},
                {"id": f"packet:{task_id}", "type": "context_packet", "format": "markdown"},
                {"id": f"agent:{planner}", "type": "planner"},
                {"id": f"agent:{executor}", "type": "executor"},
            ],
            "edges": [
                {"from": task_id, "to": "objective", "type": "implements"},
                {"from": f"packet:{task_id}", "to": task_id, "type": "guides"},
                {"from": f"agent:{planner}", "to": f"packet:{task_id}", "type": "plans"},
                {"from": f"agent:{executor}", "to": task_id, "type": "executes"},
            ],
            "allowed_edit_files": normalized_scope,
        }
        now = utc_now()
        connection = self.connect()
        cursor = connection.execute(
            """INSERT INTO context_graphs(created_at, updated_at, kind, status, graph_json, packet_markdown)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (now, now, "hierarchical_model_delegation", "PLANNED", json.dumps(graph, indent=2), compact_text(packet, CONTEXT_BUDGETS["normal"] * 4)),
        )
        connection.commit()
        graph_id = int(cursor.lastrowid)
        delegation_id = self.delegation_ref(graph_id)
        graph["id"] = delegation_id
        packet_path = self.state_dir / "delegations" / delegation_id / f"{task_id}.md"
        graph_path = self.state_dir / "delegations" / delegation_id / "graph.json"
        write_text(packet_path, packet)
        write_text(graph_path, json.dumps(graph, indent=2) + "\n")
        connection.execute(
            "UPDATE context_graphs SET graph_json = ? WHERE id = ?",
            (json.dumps(graph, indent=2), graph_id),
        )
        connection.commit()
        connection.close()
        self.send_message(planner, executor, packet, "execution_packet", task_ref=task_id)
        self.event(
            "delegation_planned",
            {"delegation_id": delegation_id, "task_id": task_id, "planner": planner, "executor": executor, "packet": str(packet_path)},
        )
        return {
            "delegation_id": delegation_id,
            "task_id": task_id,
            "planner": planner,
            "executor": executor,
            "mode": mode,
            "packet_path": str(packet_path),
            "graph_path": str(graph_path),
            "estimated_tokens": estimate_tokens(packet),
            "graph": graph,
            "packet": packet,
        }

    def get_delegation(self, delegation_ref: str) -> dict[str, Any] | None:
        graph_id = self.parse_delegation_ref(delegation_ref)
        connection = self.connect()
        row = connection.execute(
            "SELECT id, created_at, updated_at, kind, status, graph_json, packet_markdown FROM context_graphs WHERE id = ?",
            (graph_id,),
        ).fetchone()
        connection.close()
        if not row:
            return None
        return {
            "delegation_id": self.delegation_ref(row[0]),
            "created_at": row[1],
            "updated_at": row[2],
            "kind": row[3],
            "status": row[4],
            "graph": json.loads(row[5]),
            "packet": row[6],
        }

    def event(self, kind: str, payload: dict[str, Any]) -> None:
        item = {"created_at": utc_now(), "kind": kind, "payload": payload}
        connection = self.connect()
        connection.execute(
            "INSERT INTO events(created_at, kind, payload) VALUES (?, ?, ?)",
            (item["created_at"], kind, json.dumps(payload, ensure_ascii=True)),
        )
        connection.commit()
        connection.close()
        with self.jsonl_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=True) + "\n")

    def recent_events(self, limit: int = 30) -> list[dict[str, Any]]:
        if not self.db_file.exists():
            return []
        connection = self.connect()
        rows = connection.execute(
            "SELECT id, created_at, kind, payload FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        connection.close()
        return [
            {"id": row[0], "created_at": row[1], "kind": row[2], "payload": json.loads(row[3])}
            for row in reversed(rows)
        ]

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if not self.db_file.exists():
            return []
        pattern = f"%{query.lower()}%"
        connection = self.connect()
        rows = connection.execute(
            """SELECT id, created_at, kind, payload
               FROM events
               WHERE lower(kind) LIKE ? OR lower(payload) LIKE ?
               ORDER BY id DESC LIMIT ?""",
            (pattern, pattern, limit),
        ).fetchall()
        connection.close()
        return [
            {"id": row[0], "created_at": row[1], "kind": row[2], "payload": json.loads(row[3])}
            for row in rows
        ]

    def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        if not self.db_file.exists():
            return None
        connection = self.connect()
        row = connection.execute(
            "SELECT id, created_at, kind, payload FROM events WHERE id = ?", (memory_id,)
        ).fetchone()
        connection.close()
        if not row:
            return None
        return {"id": row[0], "created_at": row[1], "kind": row[2], "payload": json.loads(row[3])}

    @staticmethod
    def task_ref(task_id: int) -> str:
        return f"T{task_id:04d}"

    @staticmethod
    def parse_task_ref(value: str) -> int:
        normalized = value.strip().upper()
        if normalized.startswith("T"):
            normalized = normalized[1:]
        if not normalized.isdigit():
            raise ValueError(f"Invalid task id: {value}")
        return int(normalized)

    def create_task(self, title: str, mode: str = "sequential") -> dict[str, Any]:
        title = compact_text(title, 400)
        now = utc_now()
        connection = self.connect()
        cursor = connection.execute(
            "INSERT INTO tasks(created_at, updated_at, title, mode, status) VALUES (?, ?, ?, ?, ?)",
            (now, now, title, mode, "CREATED"),
        )
        connection.commit()
        task_id = int(cursor.lastrowid)
        connection.close()
        self.event("task_created", {"task_id": self.task_ref(task_id), "title": title, "mode": mode})
        return self.get_task(self.task_ref(task_id)) or {}

    def get_task(self, task_ref: str) -> dict[str, Any] | None:
        task_id = self.parse_task_ref(task_ref)
        connection = self.connect()
        row = connection.execute(
            "SELECT id, created_at, updated_at, title, mode, status, agent, branch, summary FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        locks = connection.execute(
            "SELECT path, agent, expires_at FROM file_locks WHERE task_id = ? ORDER BY path", (task_id,)
        ).fetchall()
        connection.close()
        if not row:
            return None
        return {
            "task_id": self.task_ref(row[0]),
            "created_at": row[1],
            "updated_at": row[2],
            "title": row[3],
            "mode": row[4],
            "status": row[5],
            "agent": row[6],
            "branch": row[7],
            "summary": row[8],
            "locked_files": [{"path": lock[0], "agent": lock[1], "expires_at": lock[2]} for lock in locks],
        }

    def list_tasks(self, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        connection = self.connect()
        if status:
            rows = connection.execute(
                "SELECT id FROM tasks WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)
            ).fetchall()
        else:
            rows = connection.execute("SELECT id FROM tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        connection.close()
        return [task for row in rows if (task := self.get_task(self.task_ref(row[0]))) is not None]

    def assign_task(self, task_ref: str, agent: str, branch: str | None = None) -> dict[str, Any]:
        task = self.get_task(task_ref)
        if not task:
            raise ValueError(f"Unknown task: {task_ref}")
        if task["status"] in FINAL_TASK_STATUSES:
            raise ValueError(f"Cannot assign final task: {task_ref}")
        connection = self.connect()
        connection.execute(
            "UPDATE tasks SET updated_at = ?, status = ?, agent = ?, branch = ? WHERE id = ?",
            (utc_now(), "ASSIGNED", agent, branch, self.parse_task_ref(task_ref)),
        )
        connection.commit()
        connection.close()
        self.event("task_assigned", {"task_id": task_ref.upper(), "agent": agent, "branch": branch})
        return self.get_task(task_ref) or {}

    def claim_files(self, task_ref: str, agent: str, files: list[str], expires_at: str | None = None) -> dict[str, Any]:
        provider = agent.split(":")[-1]
        if self.is_model_provider(provider):
            raise ValueError(f"Model provider cannot claim or edit project files: {provider}")
        task = self.get_task(task_ref)
        if not task:
            raise ValueError(f"Unknown task: {task_ref}")
        if task["status"] in FINAL_TASK_STATUSES:
            raise ValueError(f"Cannot claim files for final task: {task_ref}")
        normalized = []
        for path in files:
            if not path.strip():
                continue
            value = str(Path(path).as_posix())
            while value.startswith("./"):
                value = value[2:]
            normalized.append(value)
        normalized = sorted(set(normalized))
        if not normalized:
            raise ValueError("At least one file is required.")
        connection = self.connect()
        task_id = self.parse_task_ref(task_ref)
        conflicts = connection.execute(
            f"SELECT path, task_id, agent FROM file_locks WHERE path IN ({','.join('?' for _ in normalized)}) AND task_id != ?",
            (*normalized, task_id),
        ).fetchall()
        if conflicts:
            connection.close()
            path, owner_id, owner_agent = conflicts[0]
            raise ValueError(f"File already claimed: {path} by {self.task_ref(owner_id)} ({owner_agent})")
        now = utc_now()
        for path in normalized:
            connection.execute(
                """INSERT INTO file_locks(path, task_id, agent, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET task_id = excluded.task_id, agent = excluded.agent,
                   created_at = excluded.created_at, expires_at = excluded.expires_at""",
                (path, task_id, agent, now, expires_at),
            )
        connection.execute(
            "UPDATE tasks SET updated_at = ?, status = ?, agent = COALESCE(agent, ?) WHERE id = ?",
            (now, "RUNNING", agent, task_id),
        )
        connection.commit()
        connection.close()
        self.event("files_claimed", {"task_id": task_ref.upper(), "agent": agent, "files": normalized})
        return self.get_task(task_ref) or {}

    def set_task_status(self, task_ref: str, status: str, summary: str | None = None) -> dict[str, Any]:
        status = status.upper()
        if status not in TASK_STATUSES:
            raise ValueError(f"Invalid task status: {status}")
        if not self.get_task(task_ref):
            raise ValueError(f"Unknown task: {task_ref}")
        connection = self.connect()
        task_id = self.parse_task_ref(task_ref)
        connection.execute(
            "UPDATE tasks SET updated_at = ?, status = ?, summary = COALESCE(?, summary) WHERE id = ?",
            (utc_now(), status, compact_text(summary, 800) if summary else None, task_id),
        )
        if status in FINAL_TASK_STATUSES:
            connection.execute("DELETE FROM file_locks WHERE task_id = ?", (task_id,))
        connection.commit()
        connection.close()
        self.event("task_status", {"task_id": task_ref.upper(), "status": status, "summary": summary})
        return self.get_task(task_ref) or {}

    def is_model_provider(self, provider: str) -> bool:
        if provider in NON_WRITING_PROVIDERS:
            return True
        path = self.state_dir / "providers.json"
        if not path.exists():
            return False
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return config.get("providers", {}).get(provider, {}).get("kind") == "model"

    @staticmethod
    def external_session_ref(session_id: int) -> str:
        return f"S{session_id:04d}"

    @staticmethod
    def parse_external_session_ref(value: str) -> int:
        normalized = value.strip().upper()
        if normalized.startswith("S"):
            normalized = normalized[1:]
        if not normalized.isdigit():
            raise ValueError(f"Invalid external session id: {value}")
        return int(normalized)

    def register_external_session(
        self,
        pid: int,
        process_created_at: float,
        agent: str,
        cwd: str | None,
        command_line: str,
    ) -> dict[str, Any]:
        now = utc_now()
        connection = self.connect()
        row = connection.execute(
            "SELECT id FROM external_sessions WHERE pid = ? AND process_created_at = ?",
            (pid, process_created_at),
        ).fetchone()
        if row:
            session_id = int(row[0])
            connection.execute(
                """UPDATE external_sessions SET updated_at = ?, status = ?, agent = ?, cwd = ?,
                   command_line = ? WHERE id = ?""",
                (now, "ATTACHED", agent, cwd, compact_text(command_line, 600), session_id),
            )
        else:
            cursor = connection.execute(
                """INSERT INTO external_sessions(created_at, updated_at, pid, process_created_at, agent,
                   status, cwd, command_line, integration) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now,
                    now,
                    pid,
                    process_created_at,
                    agent,
                    "ATTACHED",
                    cwd,
                    compact_text(command_line, 600),
                    "cooperative_mcp",
                ),
            )
            session_id = int(cursor.lastrowid)
        connection.commit()
        connection.close()
        reference = self.external_session_ref(session_id)
        self.event(
            "external_session_attached",
            {"session_id": reference, "pid": pid, "agent": agent, "integration": "cooperative_mcp"},
        )
        return self.get_external_session(reference) or {}

    def get_external_session(self, session_ref: str) -> dict[str, Any] | None:
        connection = self.connect()
        row = connection.execute(
            """SELECT id, created_at, updated_at, pid, process_created_at, agent, status, cwd,
               command_line, integration, context_mode, context_path
               FROM external_sessions WHERE id = ?""",
            (self.parse_external_session_ref(session_ref),),
        ).fetchone()
        connection.close()
        if not row:
            return None
        return {
            "session_id": self.external_session_ref(row[0]),
            "created_at": row[1],
            "updated_at": row[2],
            "pid": row[3],
            "process_created_at": row[4],
            "agent": row[5],
            "status": row[6],
            "cwd": row[7],
            "command_line": row[8],
            "integration": row[9],
            "context_mode": row[10],
            "context_path": row[11],
        }

    def find_external_session(self, pid: int, process_created_at: float) -> dict[str, Any] | None:
        connection = self.connect()
        row = connection.execute(
            "SELECT id FROM external_sessions WHERE pid = ? AND process_created_at = ?",
            (pid, process_created_at),
        ).fetchone()
        connection.close()
        return self.get_external_session(self.external_session_ref(int(row[0]))) if row else None

    def list_external_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        connection = self.connect()
        rows = connection.execute("SELECT id FROM external_sessions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        connection.close()
        return [
            session
            for row in rows
            if (session := self.get_external_session(self.external_session_ref(int(row[0])))) is not None
        ]

    def update_external_session_status(self, session_ref: str, status: str) -> dict[str, Any]:
        if status not in {"ATTACHED", "STOPPED", "DETACHED"}:
            raise ValueError(f"Invalid external session status: {status}")
        connection = self.connect()
        connection.execute(
            "UPDATE external_sessions SET updated_at = ?, status = ? WHERE id = ?",
            (utc_now(), status, self.parse_external_session_ref(session_ref)),
        )
        connection.commit()
        connection.close()
        return self.get_external_session(session_ref) or {}

    def publish_external_session_context(self, session_ref: str, mode: str = "compact") -> dict[str, Any]:
        session = self.get_external_session(session_ref)
        if not session:
            raise ValueError(f"Unknown external session: {session_ref}")
        if mode not in {"compact", "normal", "deep"}:
            raise ValueError(f"Invalid context mode: {mode}")
        context = self.resume_context(mode)
        instruction = (
            "Read this Continuum context packet before acting. Continue existing work from the current state. "
            "Use the Continuum MCP tools for targeted memory retrieval and write a handoff before stopping."
        )
        path = self.state_dir / "external_sessions" / session["session_id"] / "context.md"
        content = (
            f"# External Session Context\n\nSession: `{session['session_id']}`\nAgent: `{session['agent']}`\n"
            f"PID: `{session['pid']}`\nMode: `{mode}`\nGenerated: {utc_now()}\n\n"
            f"## Instruction\n\n{instruction}\n\n## Bounded Context\n\n{context}\n"
        )
        write_text(path, content)
        connection = self.connect()
        connection.execute(
            "UPDATE external_sessions SET updated_at = ?, context_mode = ?, context_path = ? WHERE id = ?",
            (utc_now(), mode, str(path), self.parse_external_session_ref(session_ref)),
        )
        connection.commit()
        connection.close()
        self.event(
            "external_context_published",
            {"session_id": session_ref.upper(), "agent": session["agent"], "mode": mode, "path": str(path)},
        )
        return {
            "session": self.get_external_session(session_ref),
            "path": str(path),
            "mode": mode,
            "estimated_tokens": estimate_tokens(content),
            "instruction": instruction,
            "context": content,
        }

    def store_embedding(self, memory_key: str, provider: str, model: str, vector: list[float], source: str) -> None:
        connection = self.connect()
        connection.execute(
            """INSERT INTO embeddings(memory_key, created_at, provider, model, vector, source_preview)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(memory_key) DO UPDATE SET created_at = excluded.created_at,
               provider = excluded.provider, model = excluded.model, vector = excluded.vector,
               source_preview = excluded.source_preview""",
            (memory_key, utc_now(), provider, model, json.dumps(vector), compact_text(source, 240)),
        )
        connection.commit()
        connection.close()
        self.event(
            "memory_embedded",
            {"memory_key": memory_key, "provider": provider, "model": model, "dimensions": len(vector)},
        )

    @staticmethod
    def workflow_ref(workflow_id: int) -> str:
        return f"W{workflow_id:04d}"

    @staticmethod
    def parse_workflow_ref(value: str) -> int:
        normalized = value.strip().upper()
        if normalized.startswith("W"):
            normalized = normalized[1:]
        if not normalized.isdigit():
            raise ValueError(f"Invalid workflow id: {value}")
        return int(normalized)

    def create_workflow(self, team: str, request: str, task_type: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
        now = utc_now()
        connection = self.connect()
        cursor = connection.execute(
            "INSERT INTO workflows(created_at, updated_at, team, request, task_type, status) VALUES (?, ?, ?, ?, ?, ?)",
            (now, now, team, compact_text(request, 600), task_type, "PLANNED"),
        )
        workflow_id = int(cursor.lastrowid)
        for step in steps:
            connection.execute(
                "INSERT INTO workflow_steps(workflow_id, step_number, role, provider, status) VALUES (?, ?, ?, ?, ?)",
                (workflow_id, int(step["order"]), str(step["name"]), str(step["provider"]), "PLANNED"),
            )
        connection.commit()
        connection.close()
        self.event("workflow_created", {"workflow_id": self.workflow_ref(workflow_id), "team": team, "task_type": task_type})
        return self.get_workflow(self.workflow_ref(workflow_id)) or {}

    def get_workflow(self, workflow_ref: str) -> dict[str, Any] | None:
        workflow_id = self.parse_workflow_ref(workflow_ref)
        connection = self.connect()
        row = connection.execute(
            "SELECT id, created_at, updated_at, team, request, task_type, status, current_step, summary FROM workflows WHERE id = ?",
            (workflow_id,),
        ).fetchone()
        steps = connection.execute(
            "SELECT step_number, role, provider, task_id, status, output FROM workflow_steps WHERE workflow_id = ? ORDER BY step_number",
            (workflow_id,),
        ).fetchall()
        connection.close()
        if not row:
            return None
        return {
            "workflow_id": self.workflow_ref(row[0]),
            "created_at": row[1],
            "updated_at": row[2],
            "team": row[3],
            "request": row[4],
            "task_type": row[5],
            "status": row[6],
            "current_step": row[7],
            "summary": row[8],
            "steps": [
                {
                    "order": item[0], "role": item[1], "provider": item[2],
                    "task_id": self.task_ref(item[3]) if item[3] else None,
                    "status": item[4], "output": item[5],
                }
                for item in steps
            ],
        }

    def list_workflows(self, limit: int = 20) -> list[dict[str, Any]]:
        connection = self.connect()
        rows = connection.execute("SELECT id FROM workflows ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        connection.close()
        return [item for row in rows if (item := self.get_workflow(self.workflow_ref(row[0]))) is not None]

    def update_workflow(
        self, workflow_ref: str, status: str, current_step: int | None = None, summary: str | None = None
    ) -> dict[str, Any]:
        if status not in WORKFLOW_STATUSES:
            raise ValueError(f"Invalid workflow status: {status}")
        workflow_id = self.parse_workflow_ref(workflow_ref)
        connection = self.connect()
        connection.execute(
            """UPDATE workflows SET updated_at = ?, status = ?,
               current_step = COALESCE(?, current_step), summary = COALESCE(?, summary)
               WHERE id = ?""",
            (utc_now(), status, current_step, compact_text(summary, 800) if summary else None, workflow_id),
        )
        connection.commit()
        connection.close()
        self.event("workflow_status", {"workflow_id": workflow_ref, "status": status, "current_step": current_step, "summary": summary})
        return self.get_workflow(workflow_ref) or {}

    def update_workflow_step(
        self, workflow_ref: str, step_number: int, status: str, task_ref: str | None = None, output: str | None = None
    ) -> None:
        workflow_id = self.parse_workflow_ref(workflow_ref)
        task_id = self.parse_task_ref(task_ref) if task_ref else None
        connection = self.connect()
        connection.execute(
            """UPDATE workflow_steps SET status = ?, task_id = COALESCE(?, task_id),
               output = COALESCE(?, output) WHERE workflow_id = ? AND step_number = ?""",
            (status, task_id, compact_text(output, 2_400) if output else None, workflow_id, step_number),
        )
        connection.commit()
        connection.close()

    def send_message(
        self, sender: str, recipient: str, body: str, kind: str = "result",
        workflow_ref: str | None = None, task_ref: str | None = None
    ) -> dict[str, Any]:
        text = compact_text(body, CONTEXT_BUDGETS["retrieval_default"] * 4)
        connection = self.connect()
        cursor = connection.execute(
            """INSERT INTO messages(created_at, workflow_id, task_id, sender, recipient, kind, body, token_estimate)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                utc_now(),
                self.parse_workflow_ref(workflow_ref) if workflow_ref else None,
                self.parse_task_ref(task_ref) if task_ref else None,
                sender, recipient, kind, text, estimate_tokens(text),
            ),
        )
        connection.commit()
        message_id = int(cursor.lastrowid)
        connection.close()
        self.event("message_sent", {"message_id": f"MSG{message_id:04d}", "sender": sender, "recipient": recipient, "kind": kind})
        return {"message_id": f"MSG{message_id:04d}", "sender": sender, "recipient": recipient, "kind": kind, "body": text}

    def messages(self, recipient: str | None = None, workflow_ref: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        connection = self.connect()
        conditions: list[str] = []
        parameters: list[Any] = []
        if recipient:
            conditions.append("(recipient = ? OR recipient = '*')")
            parameters.append(recipient)
        if workflow_ref:
            conditions.append("workflow_id = ?")
            parameters.append(self.parse_workflow_ref(workflow_ref))
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = connection.execute(
            f"SELECT id, created_at, sender, recipient, kind, body, token_estimate FROM messages{where} ORDER BY id DESC LIMIT ?",
            (*parameters, limit),
        ).fetchall()
        connection.close()
        return [
            {"message_id": f"MSG{row[0]:04d}", "created_at": row[1], "sender": row[2], "recipient": row[3], "kind": row[4], "body": row[5], "estimated_tokens": row[6]}
            for row in reversed(rows)
        ]

    def semantic_search(self, vector: list[float], limit: int = 8, task_hint: str = "") -> list[dict[str, Any]]:
        if not vector:
            return []
        connection = self.connect()
        rows = connection.execute("SELECT memory_key, created_at, provider, model, vector, source_preview FROM embeddings").fetchall()
        connection.close()
        query_norm = math.sqrt(sum(value * value for value in vector))
        if not query_norm:
            return []
        ranked: list[dict[str, Any]] = []
        for row in rows:
            try:
                candidate = [float(value) for value in json.loads(row[4])]
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if len(candidate) != len(vector):
                continue
            denominator = query_norm * math.sqrt(sum(value * value for value in candidate))
            if not denominator:
                continue
            similarity = sum(left * right for left, right in zip(vector, candidate)) / denominator
            try:
                age_days = max((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(row[1])).total_seconds() / 86400, 0.0)
            except ValueError:
                age_days = 365.0
            recency_boost = 0.08 / (1.0 + age_days)
            task_boost = 0.05 if task_hint and task_hint.lower() in row[5].lower() else 0.0
            score = similarity + recency_boost + task_boost
            memory_id = row[0][2:] if row[0].startswith("M:") else None
            ranked.append({
                "memory_key": row[0], "memory_id": f"M{memory_id}" if memory_id else None,
                "source": f"event:{memory_id}" if memory_id else row[0],
                "created_at": row[1], "provider": row[2], "model": row[3],
                "similarity": round(similarity, 6), "score": round(score, 6), "preview": row[5],
            })
        return sorted(ranked, key=lambda item: item["score"], reverse=True)[:limit]

    def context_packet(
        self, role: str, query: str = "", mode: str = "compact", workflow_ref: str | None = None
    ) -> dict[str, Any]:
        if mode not in {"compact", "normal", "deep"}:
            raise ValueError(f"Invalid context mode: {mode}")
        sections = [f"# Continuum Context Packet\n\nRole: `{role}`\nMode: `{mode}`"]
        sections.append(self.resume_context(mode))
        inbox = self.messages(role, workflow_ref, 4 if mode == "compact" else 10)
        if inbox:
            sections.append("## Messages\n" + "\n".join(f"- {item['sender']}: {item['body']}" for item in inbox))
        if query and mode != "compact":
            matches = self.search(query, 3 if mode == "normal" else 8)
            if matches:
                sections.append("## Retrieved Memory\n" + "\n".join(f"- M{item['id']} `{item['kind']}`: {json.dumps(item['payload'], ensure_ascii=True)}" for item in matches))
        text = compact_text("\n\n".join(sections), CONTEXT_BUDGETS[mode] * 4)
        return {"role": role, "mode": mode, "estimated_tokens": estimate_tokens(text), "text": text}

    def latest_task(self) -> tuple[str, str | None] | None:
        fallback: tuple[str, str | None] | None = None
        for item in reversed(self.recent_events(100)):
            if item["kind"] == "handoff":
                task = item["payload"].get("task", "")
                latest = (task, item["payload"].get("next_step"))
                if fallback is None:
                    fallback = latest
                if not self.is_synthetic_task(task):
                    return latest
        return fallback

    @staticmethod
    def is_synthetic_task(task: str) -> bool:
        return any(pattern.match(task.strip()) for pattern in SYNTHETIC_TASK_PATTERNS)

    def write_metadata_and_index(self) -> None:
        if not self.notes_dir or not self.index_file or not self.vault_dir:
            return
        metadata = {"project": str(self.project), "project_id": self.project_id, "updated_at": utc_now()}
        write_text(self.notes_dir / "project.json", json.dumps(metadata, indent=2) + "\n")
        rows: list[dict[str, str]] = []
        for path in (self.vault_dir / "Projects").glob("*/project.json"):
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        rows.sort(key=lambda row: row.get("updated_at", ""), reverse=True)
        index = (
            "# Continuum Memory Index\n\n"
            "Open only the active project's `Current.md` first. Retrieve its handoff,\n"
            "current state or session notes only if the current task needs more context.\n\n"
            "## Projects\n"
        )
        for row in rows:
            project_folder = self.vault_dir / "Projects" / row["project_id"]
            starting_note = "Current" if (project_folder / "Current.md").exists() else "Current State"
            index += (
                f"\n- [[Projects/{row['project_id']}/{starting_note}|{row['project_id']}]]"
                f" - `{row['project']}`"
            )
        write_text(self.index_file, index + "\n")

    def git_or_watch_changes(self) -> list[str]:
        if (self.project / ".git").exists():
            completed = subprocess.run(
                ["git", "-C", str(self.project), "status", "--short"],
                capture_output=True,
                text=True,
                check=False,
            )
            changes = completed.stdout.splitlines() if completed.returncode == 0 else []
            return changes[:MAX_CHANGE_LINES] or ["No uncommitted Git changes."]
        for event in reversed(self.recent_events(20)):
            if event["kind"] == "file_changes":
                return event["payload"].get("changes", [])[:MAX_CHANGE_LINES]
        return ["No recorded file changes."]

    def scan_files(self) -> dict[str, int]:
        files: dict[str, int] = {}
        generated = self.vault_dir / "Projects" if self.vault_dir else None
        for root, directories, names in os.walk(self.project):
            directories[:] = [directory for directory in directories if directory not in IGNORE_DIRS]
            root_path = Path(root)
            if generated and (root_path == generated or generated in root_path.parents):
                directories[:] = []
                continue
            for name in names:
                path = root_path / name
                if self.index_file and path == self.index_file:
                    continue
                try:
                    files[str(path.relative_to(self.project))] = path.stat().st_mtime_ns
                except OSError:
                    continue
        return files

    def poll_changes(self) -> list[str]:
        snapshot_path = self.state_dir / "file_snapshot.json"
        prior: dict[str, int] = {}
        if snapshot_path.exists():
            try:
                prior = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        current = self.scan_files()
        changed = sorted(path for path, stamp in current.items() if prior.get(path) != stamp)
        deleted = sorted(path for path in prior if path not in current)
        write_text(snapshot_path, json.dumps(current, indent=2) + "\n")
        results = [f"Changed: {path}" for path in changed] + [f"Deleted: {path}" for path in deleted]
        if results:
            self.event("file_changes", {"changes": results[:MAX_CHANGE_LINES]})
        return results

    def write_handoff(self, task: str, next_step: str | None = None) -> None:
        task = compact_text(task)
        next_action = compact_text(next_step or "Record the next exact executable action.")
        changes = "\n".join(f"- `{item}`" for item in self.git_or_watch_changes())
        events = self.recent_events(20)
        commands = [
            f"- {item['created_at']}: {compact_text(item['payload'].get('summary', ''), 280)}"
            for item in events
            if item["kind"] in {"agent_start", "agent_exit"}
        ]
        errors = [
            f"- {item['created_at']}: {compact_text(item['payload'].get('summary', ''), 280)}"
            for item in events
            if item["kind"] == "error" or (
                item["kind"] == "agent_exit" and item["payload"].get("returncode", 0) != 0
            )
        ]
        handoff = f"""# Latest Handoff

Generated: {utc_now()}
Project: `{self.project}`

## Current Task
{task}

## Files Changed
{changes}

## Errors Found
{chr(10).join(errors) or "- No recorded errors."}

## Commands And Sessions
{chr(10).join(commands[-10:]) or "- No wrapped sessions recorded."}

## Next Step
{next_action}

## Continue Prompt
Read `.continuum/current_state.md` and `.continuum/latest_handoff.md`. Continue
the current task from the existing state and complete the next step above.
"""
        current = f"""# Current

Task: {compact_text(task, 360)}
Changes: {compact_text("; ".join(self.git_or_watch_changes()), 260)}
Blocker: {compact_text((errors[-1] if errors else "None recorded."), 180)}
Next: {compact_text(next_action, 300)}
"""
        state = f"""# Current State

Updated: {utc_now()}
Project: `{self.project}`

## Active Task
{task}

## Recent Events
""" + ("\n".join(f"- {item['created_at']} `{item['kind']}`" for item in events[-12:]) or "- None.") + "\n"
        notes = {"current.md": compact_text(current, CONTEXT_BUDGETS["startup"] * 4), "latest_handoff.md": compact_text(handoff, CONTEXT_BUDGETS["handoff"] * 4), "current_state.md": state}
        for filename, content in notes.items():
            write_text(self.state_dir / filename, content)
        if self.notes_dir:
            self.notes_dir.mkdir(parents=True, exist_ok=True)
            write_text(self.notes_dir / "Current.md", notes["current.md"])
            write_text(self.notes_dir / "Latest Handoff.md", notes["latest_handoff.md"])
            write_text(self.notes_dir / "Current State.md", state)
            self.write_metadata_and_index()

    def resume_context(self, mode: str) -> str:
        budget = CONTEXT_BUDGETS[mode] * 4
        current = (self.state_dir / "current.md").read_text(encoding="utf-8") if (self.state_dir / "current.md").exists() else ""
        if mode == "compact":
            return compact_text(current, budget)
        handoff = (self.state_dir / "latest_handoff.md").read_text(encoding="utf-8") if (self.state_dir / "latest_handoff.md").exists() else ""
        if mode == "normal":
            return compact_text(current + "\n" + handoff, budget)
        state = (self.state_dir / "current_state.md").read_text(encoding="utf-8") if (self.state_dir / "current_state.md").exists() else ""
        return compact_text(current + "\n" + handoff + "\n" + state, budget)

    def write_session_note(
        self, session_id: str, agent: str, returncode: int, tokens: int, output_tail: list[str]
    ) -> None:
        if not self.notes_dir:
            return
        excerpt = compact_text("".join(output_tail).strip(), 4_000) or "No captured output."
        note = f"""# Session {session_id}

Agent: `{agent}`
Exit code: `{returncode}`
Estimated recorded tokens: `{tokens}`

## Output Tail

```text
{excerpt}
```
"""
        write_text(self.notes_dir / "Sessions" / f"{session_id}.md", note)
