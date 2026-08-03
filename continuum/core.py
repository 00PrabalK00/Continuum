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

from .policy import Policy, load_policy
from .secrets_scan import redact_text, summarize_findings

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

    def policy(self) -> Policy:
        """Load the effective governance policy for this project (cached)."""
        cached = getattr(self, "_policy_cache", None)
        if cached is None:
            cached = load_policy(self.state_dir)
            self._policy_cache = cached
        return cached

    def read_context_file(self, relpath: str) -> str:
        """Return a project file's content for inclusion in egress context, with
        two governance guards applied: files matching the policy `sensitive_globs`
        have their content excluded entirely (replaced with a note), and any
        included content is passed through the secret scrubber. Use this instead
        of reading a file directly whenever file content may leave the machine."""
        normalized = str(Path(relpath).as_posix())
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if self.policy().is_sensitive_file(normalized):
            self.event("sensitive_excluded", {"path": normalized})
            return f"[content excluded by policy sensitive_globs: {normalized}]"
        path = self.project / normalized
        if not path.exists():
            return f"[file not found: {normalized}]"
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            return f"[could not read {normalized}: {error}]"
        return self.scrub_egress(content, context=f"file:{normalized}")

    def scrub_egress(self, text: str, *, context: str = "context") -> str:
        """Redact secrets from text before it leaves the local machine.

        Runs at the egress boundary (hosted-provider packets, external-session
        context, delegation packets). Records a `secret_redacted` audit event
        with the count and types only -- never the secret value itself.
        """
        if not text:
            return text
        cleaned, findings = redact_text(text)
        if findings:
            self.event(
                "secret_redacted",
                {"context": context, "count": len(findings), "types": summarize_findings(findings)},
            )
        return cleaned

    def initialize(self, context_limit: int, threshold: float) -> None:
        self.project.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(exist_ok=True)
        (self.state_dir / "session_logs").mkdir(exist_ok=True)
        # Keep machine-local state (sqlite, worktrees, session logs) out of Git so it
        # never dirties the project tree or registers worktrees as embedded repositories.
        gitignore = self.state_dir / ".gitignore"
        if not gitignore.exists():
            write_text(gitignore, "*\n")
        # Merge rather than replace: re-initializing an existing project must not
        # drop settings written since, such as the handoff model or which agents
        # have been connected to the MCP server.
        config = self.read_config()
        config.update(
            {
                "project": str(self.project),
                "project_id": self.project_id,
                "vault_dir": str(self.vault_dir) if self.vault_dir else None,
                "context_limit": context_limit,
                "checkpoint_threshold": threshold,
                "updated_at": utc_now(),
            }
        )
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
        # Only seed a starter handoff for a project that has none. Overwriting it
        # would discard recorded work, and the status card would keep showing the
        # lost task because it reads the event log rather than these files.
        if not (self.state_dir / "latest_handoff.md").exists():
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
        connection.execute(
            """CREATE TABLE IF NOT EXISTS agent_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session TEXT NOT NULL,
                agent TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                injected_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_tokens INTEGER NOT NULL DEFAULT 0,
                estimate_quality TEXT NOT NULL DEFAULT 'piped',
                checkpoint_triggered INTEGER NOT NULL DEFAULT 0,
                returncode INTEGER,
                UNIQUE(session)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS agent_limit_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                agent TEXT NOT NULL,
                session TEXT,
                kind TEXT NOT NULL,
                evidence TEXT NOT NULL,
                reset_at TEXT,
                confirmed INTEGER NOT NULL DEFAULT 0
            )"""
        )
        connection.commit()
        return connection

    def record_agent_usage(
        self,
        *,
        session: str,
        agent: str,
        started_at: str,
        ended_at: str,
        injected_tokens: int = 0,
        output_tokens: int = 0,
        estimate_quality: str = "piped",
        checkpoint_triggered: bool = False,
        returncode: int | None = None,
    ) -> None:
        """Record what one session consumed, as far as Continuum could see it.

        `token_usage.json` keeps only the last session, so it cannot answer
        anything about accumulation. This can, and it never overwrites.
        """
        connection = self.connect()
        connection.execute(
            """INSERT INTO agent_usage(session, agent, started_at, ended_at, injected_tokens,
               output_tokens, estimated_tokens, estimate_quality, checkpoint_triggered, returncode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session) DO UPDATE SET ended_at = excluded.ended_at,
               injected_tokens = excluded.injected_tokens, output_tokens = excluded.output_tokens,
               estimated_tokens = excluded.estimated_tokens,
               checkpoint_triggered = excluded.checkpoint_triggered,
               returncode = excluded.returncode""",
            (session, agent, started_at, ended_at, int(injected_tokens), int(output_tokens),
             int(injected_tokens) + int(output_tokens), estimate_quality,
             1 if checkpoint_triggered else 0, returncode),
        )
        connection.commit()
        connection.close()

    def record_limit_signal(
        self,
        *,
        agent: str,
        kind: str,
        evidence: str,
        session: str | None = None,
        reset_at: str | None = None,
        confirmed: bool = False,
    ) -> None:
        """Record, verbatim, what an agent said when it hit a limit.

        Unconfirmed observations are stored too. They are what lets a user see
        why Continuum did or did not route around an agent; only ranking filters
        them out.
        """
        connection = self.connect()
        connection.execute(
            """INSERT INTO agent_limit_signals(observed_at, agent, session, kind, evidence,
               reset_at, confirmed) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (utc_now(), agent, session, kind, compact_text(evidence, 200), reset_at,
             1 if confirmed else 0),
        )
        connection.commit()
        connection.close()

    def agent_usage_since(self, since: str, agent: str | None = None) -> list[dict[str, Any]]:
        if not self.db_file.exists():
            return []
        connection = self.connect()
        try:
            query = "SELECT session, agent, started_at, ended_at, estimated_tokens, " \
                    "estimate_quality, checkpoint_triggered, returncode FROM agent_usage " \
                    "WHERE ended_at >= ?"
            params: list[Any] = [since]
            if agent:
                query += " AND agent = ?"
                params.append(agent)
            rows = connection.execute(query + " ORDER BY ended_at DESC", params).fetchall()
        except sqlite3.Error:
            return []
        finally:
            connection.close()
        keys = ("session", "agent", "started_at", "ended_at", "estimated_tokens",
                "estimate_quality", "checkpoint_triggered", "returncode")
        return [dict(zip(keys, row)) for row in rows]

    def limit_signals_since(self, since: str, agent: str | None = None) -> list[dict[str, Any]]:
        """Signals observed since `since`, plus any whose stated reset is still
        in the future.

        A message like "resets in 1 day" outlives the usage window it was seen
        in. Querying on observation time alone would let the agent look
        unencumbered again while it is still, by its own account, blocked.
        """
        if not self.db_file.exists():
            return []
        connection = self.connect()
        try:
            query = ("SELECT observed_at, agent, session, kind, evidence, reset_at, confirmed "
                     "FROM agent_limit_signals WHERE (observed_at >= ? OR reset_at > ?)")
            params: list[Any] = [since, utc_now()]
            if agent:
                query += " AND agent = ?"
                params.append(agent)
            rows = connection.execute(query + " ORDER BY observed_at DESC", params).fetchall()
        except sqlite3.Error:
            return []
        finally:
            connection.close()
        keys = ("observed_at", "agent", "session", "kind", "evidence", "reset_at", "confirmed")
        return [dict(zip(keys, row)) for row in rows]

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
        packet = self.scrub_egress(packet, context="delegation_packet")
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
        cursor = connection.execute(
            "INSERT INTO events(created_at, kind, payload) VALUES (?, ?, ?)",
            (item["created_at"], kind, json.dumps(payload, ensure_ascii=True)),
        )
        event_id = cursor.lastrowid
        connection.commit()
        connection.close()
        with self.jsonl_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=True) + "\n")
        # Keep the meaning-based index level with the log. Hooking here rather
        # than at each of the several call sites that record handoffs means a
        # new one cannot silently go unindexed.
        if kind == "handoff" and event_id is not None:
            self.embed_handoff(int(event_id), str(payload.get("task") or ""))

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
        policy = self.policy()
        if not policy.provider_allowed(provider):
            self.event("policy_denied_provider", {"task_id": task_ref.upper(), "agent": agent, "provider": provider})
            raise ValueError(f"Provider not allowed by policy: {provider}")
        task = self.get_task(task_ref)
        if not task:
            raise ValueError(f"Unknown task: {task_ref}")
        if task["status"] in FINAL_TASK_STATUSES:
            raise ValueError(f"Cannot claim files for final task: {task_ref}")
        from .evidence import is_absolute, path_parts

        normalized = []
        for path in files:
            if not path.strip():
                continue
            value = str(Path(path).as_posix())
            while value.startswith("./"):
                value = value[2:]
            # An absolute claim cannot be compared with the project-relative
            # paths Git reports, and accepting one would let a claim outside the
            # project appear to cover an edit inside it.
            if is_absolute(value):
                raise ValueError(
                    f"Claim paths are relative to the project: {path}. "
                    "Use src/app.py rather than an absolute path."
                )
            normalized.append(value)
        normalized = sorted(set(normalized))
        if not normalized:
            raise ValueError("At least one file is required.")
        for path in normalized:
            if policy.is_denied_file(path):
                self.event("policy_denied_claim", {"task_id": task_ref.upper(), "agent": agent, "path": path})
                raise ValueError(f"File denied by policy and cannot be claimed: {path}")
        connection = self.connect()
        task_id = self.parse_task_ref(task_ref)
        # Exact matches are not the whole conflict. A claim on a directory owns
        # everything beneath it, so `src` and `src/app.py` held by two tasks
        # would let both present the same file as in scope, which is the
        # exclusive-claim model gone.
        existing = connection.execute(
            "SELECT path, task_id, agent FROM file_locks WHERE task_id != ?", (task_id,)
        ).fetchall()
        for path in normalized:
            wanted = path_parts(path)
            for other, owner_id, owner_agent in existing:
                theirs = path_parts(other)
                shorter, longer = sorted((wanted, theirs), key=len)
                if shorter and longer[: len(shorter)] == shorter:
                    connection.close()
                    relation = "is already claimed" if other == path else (
                        f"overlaps `{other}`, which is claimed"
                    )
                    raise ValueError(
                        f"File {relation} by {self.task_ref(owner_id)} ({owner_agent}): {path}"
                    )
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

    def list_claims(self) -> list[dict[str, Any]]:
        """Return every active file claim with its owning task, agent and age."""
        connection = self.connect()
        rows = connection.execute(
            """SELECT locks.path, locks.task_id, locks.agent, locks.created_at, locks.expires_at, tasks.status
               FROM file_locks AS locks LEFT JOIN tasks ON tasks.id = locks.task_id
               ORDER BY locks.path""",
        ).fetchall()
        connection.close()
        return [
            {
                "path": row[0],
                "task_id": self.task_ref(row[1]),
                "agent": row[2],
                "since": row[3],
                "expires_at": row[4],
                "task_status": row[5],
            }
            for row in rows
        ]

    def release_claim(
        self,
        task_ref: str,
        reason: str,
        file: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Release a task's file claims (or one path) and record an audit reason.

        Conservative by design: a claim owned by a task that is still RUNNING is
        only released when `force` is set, so a live agent's locks are not pulled
        out from under it by accident. The `claim_released` event always carries
        the supplied reason so every release is auditable.
        """
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("A release reason is required.")
        task = self.get_task(task_ref)
        if not task:
            raise ValueError(f"Unknown task: {task_ref}")
        task_id = self.parse_task_ref(task_ref)
        if task["status"] == "RUNNING" and not force:
            raise ValueError(
                f"Task {task_ref.upper()} is RUNNING; refusing to release its claims without --force."
            )
        connection = self.connect()
        if file is not None:
            value = str(Path(file).as_posix())
            while value.startswith("./"):
                value = value[2:]
            rows = connection.execute(
                "SELECT path FROM file_locks WHERE task_id = ? AND path = ?", (task_id, value)
            ).fetchall()
            released = [row[0] for row in rows]
            connection.execute(
                "DELETE FROM file_locks WHERE task_id = ? AND path = ?", (task_id, value)
            )
        else:
            rows = connection.execute(
                "SELECT path FROM file_locks WHERE task_id = ? ORDER BY path", (task_id,)
            ).fetchall()
            released = [row[0] for row in rows]
            connection.execute("DELETE FROM file_locks WHERE task_id = ?", (task_id,))
        connection.commit()
        connection.close()
        self.event(
            "claim_released",
            {
                "task_id": task_ref.upper(),
                "agent": task["agent"],
                "files": released,
                "reason": compact_text(reason, 400),
                "forced": bool(force and task["status"] == "RUNNING"),
            },
        )
        return {"task_id": task_ref.upper(), "released": released, "reason": reason}

    def recover_stale_claims(self, reason: str) -> list[dict[str, Any]]:
        """Release orphaned claims: locks whose owning task is in a terminal state
        (DONE/FAILED) but whose locks were never cleaned up. Never touches claims
        for live (non-terminal) tasks."""
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("A recovery reason is required.")
        connection = self.connect()
        rows = connection.execute(
            f"""SELECT locks.task_id, locks.path, tasks.status
                FROM file_locks AS locks JOIN tasks ON tasks.id = locks.task_id
                WHERE tasks.status IN ({','.join('?' for _ in FINAL_TASK_STATUSES)})
                ORDER BY locks.task_id, locks.path""",
            tuple(sorted(FINAL_TASK_STATUSES)),
        ).fetchall()
        connection.close()
        by_task: dict[int, dict[str, Any]] = {}
        for task_id, path, status in rows:
            entry = by_task.setdefault(task_id, {"status": status, "paths": []})
            entry["paths"].append(path)
        recovered: list[dict[str, Any]] = []
        for task_id, entry in sorted(by_task.items()):
            task_ref = self.task_ref(task_id)
            result = self.release_claim(task_ref, f"stale-recover ({entry['status']}): {reason}")
            self.event(
                "claim_recovered",
                {"task_id": task_ref, "task_status": entry["status"], "files": result["released"]},
            )
            recovered.append({"task_id": task_ref, "task_status": entry["status"], "released": result["released"]})
        return recovered

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
        content = self.scrub_egress(content, context="external_session")
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

    def has_embedding(self, memory_key: str) -> bool:
        if not self.db_file.exists():
            return False
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT 1 FROM embeddings WHERE memory_key = ? LIMIT 1", (memory_key,)
            ).fetchone()
        except sqlite3.Error:
            return False
        finally:
            connection.close()
        return row is not None

    def embed_handoff(self, event_id: int, task: str) -> None:
        """Embed one handoff as it is recorded, when a local model is available.

        Indexing only at setup time means every decision taken afterwards is
        invisible to meaning-based search, while setup still reports that search
        matches meaning. Doing it here keeps the index level with the log.

        This is best-effort by design: no embedding model, no Ollama, or a
        read-only store all leave search working on wording alone.
        """
        if not task.strip():
            return
        memory_key = f"M:{event_id}"
        try:
            from .providers import ProviderError, ProviderManager

            if not self.embeddings_enabled():
                return
            model, vector = ProviderManager(self.state_dir, self).embed("ollama", task)
            self.store_embedding(memory_key, "ollama", model, vector, task)
        except (ProviderError, OSError, ValueError, sqlite3.Error):
            return

    def embeddings_enabled(self) -> bool:
        """True when this project has opted into embeddings by having some."""
        if not self.db_file.exists():
            return False
        connection = self.connect()
        try:
            count = int(connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])
        except sqlite3.Error:
            return False
        finally:
            connection.close()
        return count > 0

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
            f"""SELECT id, created_at, workflow_id, task_id, sender, recipient, kind, body, token_estimate
                FROM messages{where} ORDER BY id DESC LIMIT ?""",
            (*parameters, limit),
        ).fetchall()
        connection.close()
        return [
            {
                "message_id": f"MSG{row[0]:04d}",
                "created_at": row[1],
                "workflow_id": self.workflow_ref(row[2]) if row[2] else None,
                "task_id": self.task_ref(row[3]) if row[3] else None,
                "sender": row[4],
                "recipient": row[5],
                "kind": row[6],
                "body": row[7],
                "estimated_tokens": row[8],
            }
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
        self,
        role: str,
        query: str = "",
        mode: str = "compact",
        workflow_ref: str | None = None,
        files: list[str] | None = None,
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
        for relpath in files or []:
            if not relpath.strip():
                continue
            # read_context_file already excludes sensitive_globs content and scrubs
            # secrets, so this file content is safe to include.
            sections.append(f"## File `{relpath}`\n```\n{self.read_context_file(relpath)}\n```")
        text = self.scrub_egress("\n\n".join(sections), context="context_packet")
        if self.policy().max_context_tokens:
            text = compact_text(text, min(CONTEXT_BUDGETS[mode] * 4, self.policy().max_context_tokens * 4))
        else:
            text = compact_text(text, CONTEXT_BUDGETS[mode] * 4)
        return {"role": role, "mode": mode, "estimated_tokens": estimate_tokens(text), "text": text}

    DEFAULT_BRANCH = "main"

    @property
    def branch_file(self) -> Path:
        return self.state_dir / "branch"

    def current_branch(self) -> str:
        """The context line checkpoints are recorded on and read from."""
        if self.branch_file.exists():
            name = self.branch_file.read_text(encoding="utf-8").strip()
            if name:
                return name
        return self.DEFAULT_BRANCH

    def set_branch(self, name: str) -> None:
        write_text(self.branch_file, name.strip() + "\n")

    def branches(self) -> list[str]:
        """Every branch that has a checkpoint, plus the current one."""
        found = {self.current_branch(), self.DEFAULT_BRANCH}
        if self.db_file.exists():
            connection = self.connect()
            rows = connection.execute(
                "SELECT DISTINCT COALESCE(json_extract(payload, '$.branch'), ?) "
                "FROM events WHERE kind = 'handoff'",
                (self.DEFAULT_BRANCH,),
            ).fetchall()
            connection.close()
            found.update(row[0] for row in rows if row[0])
        return sorted(found)

    def recent_handoffs(self, limit: int = 30, branch: str | None = None,
                        every_branch: bool = False) -> list[dict[str, Any]]:
        """Recent handoff events on one branch, newest first.

        Selecting on kind rather than slicing the tail of the event log matters
        because a busy project can record hundreds of ordinary events between
        two handoffs, which would push the earlier handoff out of any fixed
        window over all events.

        Scoped to the current branch by default, so every existing caller reads
        the line it is working on rather than a mixture of everyone's. A project
        that never branches records nothing but the default, and behaves exactly
        as before. Checkpoints written before branches existed have no branch
        recorded and belong to the default one.
        """
        if not self.db_file.exists():
            return []
        connection = self.connect()
        if every_branch:
            rows = connection.execute(
                "SELECT id, created_at, kind, payload FROM events WHERE kind = 'handoff' "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id, created_at, kind, payload FROM events WHERE kind = 'handoff' "
                "AND COALESCE(json_extract(payload, '$.branch'), ?) = ? "
                "ORDER BY id DESC LIMIT ?",
                (self.DEFAULT_BRANCH, branch or self.current_branch(), limit),
            ).fetchall()
        connection.close()
        return [
            {"id": row[0], "created_at": row[1], "kind": row[2], "payload": json.loads(row[3])}
            for row in rows
        ]

    def handoffs_mentioning(self, text: str) -> list[dict[str, Any]]:
        """Every handoff whose payload contains the text, oldest first.

        The whole history rather than a recent window. Provenance answered from
        a window is wrong in two directions at once: a term recorded only before
        the window reads as never recorded, and a term repeated inside it is
        attributed to the later mention as though that were the first.

        SQLite does the narrowing on the raw payload, which can match a key or a
        field the caller did not ask about, so the caller filters the survivors.
        """
        if not self.db_file.exists() or not text:
            return []
        connection = self.connect()
        rows = connection.execute(
            "SELECT id, created_at, kind, payload FROM events "
            "WHERE kind = 'handoff' AND lower(payload) LIKE ? ORDER BY id ASC",
            (f"%{text.lower()}%",),
        ).fetchall()
        connection.close()
        return [
            {"id": row[0], "created_at": row[1], "kind": row[2], "payload": json.loads(row[3])}
            for row in rows
        ]

    def handoffs_mentioning(self, text: str) -> list[dict[str, Any]]:
        """Every handoff whose payload contains the text, oldest first.

        The whole history rather than a recent window. Provenance answered from
        a window is wrong in two directions at once: a term recorded only before
        the window reads as never recorded, and a term repeated inside it is
        attributed to the later mention as though that were the first.

        SQLite does the narrowing on the raw payload, which can match a key or a
        field the caller did not ask about, so the caller filters the survivors.
        """
        if not self.db_file.exists() or not text:
            return []
        connection = self.connect()
        rows = connection.execute(
            "SELECT id, created_at, kind, payload FROM events "
            "WHERE kind = 'handoff' AND lower(payload) LIKE ? ORDER BY id ASC",
            (f"%{text.lower()}%",),
        ).fetchall()
        connection.close()
        return [
            {"id": row[0], "created_at": row[1], "kind": row[2], "payload": json.loads(row[3])}
            for row in rows
        ]

    def earlier_tasks(self, limit: int = 4, exclude: str = "") -> list[str]:
        """Distinct earlier handoff tasks, most recent first.

        Compact context otherwise carries only the current task, so a decision
        taken a few sessions ago disappears once enough events pile up on top of
        it. Keeping a short trail of what was previously being worked on is what
        lets an agent answer questions about earlier sessions without going and
        reading the raw event log.
        """
        # Callers pass the task after it has been truncated for storage, while
        # the recorded event still holds the full text. Both sides are put
        # through the same truncation so a long task is not selected as its own
        # earlier entry.
        normalized_exclude = compact_text(exclude).lower()
        seen: list[str] = []
        for item in self.recent_handoffs(60):
            task = compact_text(str(item["payload"].get("task") or "").strip())
            if not task or self.is_synthetic_task(task):
                continue
            if task.lower() == normalized_exclude or task in seen:
                continue
            seen.append(task)
            if len(seen) >= limit:
                break
        return seen

    def latest_task(self) -> tuple[str, str | None] | None:
        """The newest recorded task on the current branch.

        Through recent_handoffs rather than the last hundred events of any kind,
        so that a burst of ordinary activity cannot push the handoff out of the
        window, and so that this follows the branch like every other read.
        """
        fallback: tuple[str, str | None] | None = None
        for item in self.recent_handoffs(60):
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
        earlier = self.earlier_tasks(exclude=task)
        earlier_line = (
            "Earlier: " + compact_text("; ".join(earlier), 420) + "\n" if earlier else ""
        )
        current = f"""# Current

Task: {compact_text(task, 360)}
Changes: {compact_text("; ".join(self.git_or_watch_changes()), 260)}
Blocker: {compact_text((errors[-1] if errors else "None recorded."), 180)}
{earlier_line}Next: {compact_text(next_action, 300)}
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
            return self._with_drift(compact_text(current, budget))
        handoff = (self.state_dir / "latest_handoff.md").read_text(encoding="utf-8") if (self.state_dir / "latest_handoff.md").exists() else ""
        if mode == "normal":
            return self._with_drift(compact_text(current + "\n" + handoff, budget))
        state = (self.state_dir / "current_state.md").read_text(encoding="utf-8") if (self.state_dir / "current_state.md").exists() else ""
        return self._with_drift(compact_text(current + "\n" + handoff + "\n" + state, budget))

    def _with_drift(self, context: str) -> str:
        """Prepend the staleness warning, if there is one to give.

        The agent receiving this cannot tell how old the context is, and the
        text reads as present tense whether it was written a minute or a month
        ago. Added before truncation would risk the warning being the part cut,
        so it goes on afterwards.
        """
        from .freshness import age_note, describe

        notes = [note for note in (age_note(self), describe(self)) if note]
        if not notes or not context:
            return context
        return " ".join(notes) + "\n\n" + context

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
