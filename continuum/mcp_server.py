"""Minimal MCP stdio server for project-scoped Continuum memory."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import IO, Any

from . import __version__
from .core import CONTEXT_BUDGETS, MemoryStore, compact_text

SERVER_INFO = {"name": "continuum", "version": __version__}
PROTOCOL_VERSION = "2025-03-26"


def read_note(path: Path) -> str:
    if not path.exists():
        return "No memory has been written yet."
    return compact_text(path.read_text(encoding="utf-8"), 12_000)


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "get_startup_context",
            "description": "Read the smallest current task context. Use this first.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_current_state",
            "description": "Read compact project state when startup context is insufficient.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_latest_handoff",
            "description": "Read the compact latest handoff and next step for this project.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "search_memory",
            "description": (
                "Search recorded project memory by meaning and by wording, and return compressed "
                "matching memory IDs for targeted expansion. Ask in your own words; an exact "
                "phrase from the log is not required."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
        },
        {
            "name": "expand_memory",
            "description": "Expand one memory event by its ID after a targeted search.",
            "inputSchema": {
                "type": "object",
                "properties": {"memory_id": {"type": "integer"}},
                "required": ["memory_id"],
            },
        },
        {
            "name": "get_raw_log",
            "description": "Read a bounded tail of one named local session log for debugging only.",
            "inputSchema": {
                "type": "object",
                "properties": {"filename": {"type": "string"}},
                "required": ["filename"],
            },
        },
        {
            "name": "write_handoff",
            "description": "Write a deliberate project handoff after completing or pausing work.",
            "inputSchema": {
                "type": "object",
                "properties": {"task": {"type": "string"}, "next_step": {"type": "string"}},
                "required": ["task", "next_step"],
            },
        },
        {
            "name": "get_open_tasks",
            "description": "List non-final controlled tasks in this project.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_context_packet",
            "description": "Read bounded role-specific context, including only relevant workflow messages.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["compact", "normal", "deep"]},
                    "workflow_id": {"type": "string"},
                },
                "required": ["role"],
            },
        },
        {
            "name": "get_workflows",
            "description": "List recently planned or executed team workflows and their step state.",
            "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
        },
        {
            "name": "post_agent_message",
            "description": "Post a bounded result or instruction to a workflow role.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sender": {"type": "string"},
                    "recipient": {"type": "string"},
                    "body": {"type": "string"},
                    "kind": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "task_id": {"type": "string"},
                },
                "required": ["sender", "recipient", "body"],
            },
        },
        {
            "name": "get_agent_messages",
            "description": "Read bounded result messages addressed to a workflow role.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "recipient": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["recipient"],
            },
        },
        {
            "name": "claim_task_files",
            "description": "Claim specific files for one assigned agent task. Conflicting claims are rejected.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "agent": {"type": "string"},
                    "files": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["task_id", "agent", "files"],
            },
        },
        {
            "name": "complete_task",
            "description": "Mark a task DONE and release its file claims.",
            "inputSchema": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}, "summary": {"type": "string"}},
                "required": ["task_id", "summary"],
            },
        },
        {
            "name": "list_agents",
            "description": (
                "List the agent CLIs Continuum can reach on this machine. Use before ask_agent "
                "to see which other AI agents are available to consult."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "ask_agent",
            "description": (
                "Put a request to a different AI agent CLI and return its full reply. The other "
                "agent receives the same bounded project context you have, so describe what you "
                "want rather than restating the project. Use it to delegate work, get a second "
                "opinion, or continue in another agent when you are near your context limit. The "
                "exchange is recorded in shared memory, so the other agent can read it later."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "Agent CLI name, from list_agents."},
                    "request": {"type": "string", "description": "What you want the other agent to do or answer."},
                    "sender": {"type": "string", "description": "Your own agent name, for the record."},
                    "mode": {"type": "string", "enum": ["compact", "normal", "deep"]},
                    "timeout_seconds": {"type": "integer", "minimum": 10, "maximum": 1800},
                },
                "required": ["agent", "request"],
            },
        },
        {
            "name": "list_teams",
            "description": (
                "List the multi-agent teams available in this project, with the role each agent "
                "plays and which provider runs it. Use before plan_workflow to pick a team."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "plan_workflow",
            "description": (
                "Plan a multi-agent workflow for a request without running anything. Returns the "
                "ordered steps, the provider assigned to each, and a workflow id. Nothing is "
                "executed and no file is touched, so this is safe to call to see what a team "
                "would do."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "team": {"type": "string", "description": "Team name, from list_teams."},
                    "request": {"type": "string", "description": "What the team should accomplish."},
                    "task_type": {"type": "string", "description": "Optional route override."},
                },
                "required": ["team", "request"],
            },
        },
        {
            "name": "run_workflow",
            "description": (
                "Run the workflow that plan_workflow returned, executing each step's provider in "
                "order with bounded context. Pass the workflow_id you were given, so the plan you "
                "saw is the plan that runs. Roles that edit files may only write the paths listed "
                "in allow_files, and a step that touches anything else fails the workflow. Pass an "
                "empty list to run a read-only workflow. This starts other AI agents and can "
                "change files, so confirm the plan with plan_workflow first."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workflow_id": {
                        "type": "string",
                        "description": "The id returned by plan_workflow, for example W0001.",
                    },
                    "allow_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Exact paths a writing role may edit. Empty means read-only.",
                    },
                    "context_mode": {"type": "string", "enum": ["compact", "normal", "deep"]},
                },
                "required": ["workflow_id", "allow_files"],
            },
        },
        {
            "name": "get_workflow",
            "description": "Read one workflow's steps and their status by id.",
            "inputSchema": {
                "type": "object",
                "properties": {"workflow_id": {"type": "string"}},
                "required": ["workflow_id"],
            },
        },
        {
            "name": "get_external_sessions",
            "description": "List manually launched agent sessions explicitly attached to this project.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_external_session_context",
            "description": "Read the bounded context packet prepared for one attached external session.",
            "inputSchema": {
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        },
    ]


def call_tool(store: MemoryStore, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "get_startup_context":
        text = compact_text(read_note(store.state_dir / "current.md"), CONTEXT_BUDGETS["startup"] * 4)
    elif name == "get_current_state":
        text = compact_text(read_note(store.state_dir / "current_state.md"), 8_000)
    elif name == "get_latest_handoff":
        text = read_note(store.state_dir / "latest_handoff.md")
    elif name == "search_memory":
        from .retrieval import search as search_memory

        results, strategy = search_memory(store, str(arguments.get("query", "")), int(arguments.get("limit", 8)))
        text = "\n".join(
            f"M{item['id']} {item['created_at']} {item['kind']}: {json.dumps(item['payload'], ensure_ascii=True)}"
            for item in results
        ) or "No matching local memory events."
        text = compact_text(f"Matched by {strategy}.\n" + text, CONTEXT_BUDGETS["retrieval_default"] * 4)
    elif name == "expand_memory":
        event = store.get_memory(int(arguments.get("memory_id", 0)))
        text = json.dumps(event, ensure_ascii=True, indent=2) if event else "Memory not found."
        text = compact_text(text, CONTEXT_BUDGETS["retrieval_default"] * 4)
    elif name == "get_raw_log":
        filename = Path(str(arguments.get("filename", ""))).name
        path = store.state_dir / "session_logs" / filename
        if not path.exists():
            text = "Log not found."
        else:
            text = compact_text(path.read_text(encoding="utf-8", errors="replace"), CONTEXT_BUDGETS["raw_log_default"] * 4)
    elif name == "write_handoff":
        task = str(arguments.get("task", "")).strip()
        next_step = str(arguments.get("next_step", "")).strip()
        if not task or not next_step:
            raise ValueError("task and next_step are required")
        store.event("handoff", {"task": task, "next_step": next_step, "source": "mcp"})
        store.write_handoff(task, next_step)
        text = "Handoff written."
    elif name == "get_open_tasks":
        tasks = [task for task in store.list_tasks(limit=20) if task["status"] not in {"DONE", "FAILED"}]
        text = "\n".join(
            f"{task['task_id']} {task['status']} {task['title']} agent={task['agent'] or 'unassigned'}"
            for task in tasks
        ) or "No open tasks."
        text = compact_text(text, CONTEXT_BUDGETS["retrieval_default"] * 4)
    elif name == "get_context_packet":
        packet = store.context_packet(
            str(arguments.get("role", "")).strip(),
            str(arguments.get("query", "")),
            str(arguments.get("mode", "compact")),
            str(arguments.get("workflow_id")) if arguments.get("workflow_id") else None,
        )
        text = f"Estimated context: {packet['estimated_tokens']} tokens\n\n{packet['text']}"
    elif name == "get_workflows":
        workflows = store.list_workflows(int(arguments.get("limit", 10)))
        text = "\n".join(
            f"{item['workflow_id']} {item['status']} team={item['team']} step={item['current_step']}: {item['request']}"
            for item in workflows
        ) or "No workflows."
        text = compact_text(text, CONTEXT_BUDGETS["retrieval_default"] * 4)
    elif name == "post_agent_message":
        message = store.send_message(
            str(arguments.get("sender", "")),
            str(arguments.get("recipient", "")),
            str(arguments.get("body", "")),
            str(arguments.get("kind", "result")),
            str(arguments.get("workflow_id")) if arguments.get("workflow_id") else None,
            str(arguments.get("task_id")) if arguments.get("task_id") else None,
        )
        text = f"{message['message_id']} recorded for {message['recipient']}."
    elif name == "get_agent_messages":
        messages = store.messages(
            str(arguments.get("recipient", "")),
            str(arguments.get("workflow_id")) if arguments.get("workflow_id") else None,
            int(arguments.get("limit", 10)),
        )
        text = "\n".join(
            f"{item['message_id']} {item['sender']} -> {item['recipient']}: {item['body']}" for item in messages
        ) or "No messages."
        text = compact_text(text, CONTEXT_BUDGETS["retrieval_default"] * 4)
    elif name == "claim_task_files":
        claimed = store.claim_files(
            str(arguments.get("task_id", "")),
            str(arguments.get("agent", "")),
            [str(path) for path in arguments.get("files", [])],
        )
        text = f"{claimed['task_id']} RUNNING; claimed {len(claimed['locked_files'])} file(s)."
    elif name == "complete_task":
        completed = store.set_task_status(
            str(arguments.get("task_id", "")), "DONE", str(arguments.get("summary", ""))
        )
        text = f"{completed['task_id']} DONE; file claims released."
    elif name == "list_agents":
        from .agents import installed_agents, read_agents

        known = read_agents(store)
        installed = set(installed_agents(store))
        rows = [
            f"{agent}: {'available' if agent in installed else 'not installed'} (prompt via {spec['inject']})"
            for agent, spec in sorted(known.items())
        ]
        text = "\n".join(rows) or "No agent CLIs are registered."
        if not installed:
            text += "\nNone are installed on this machine, so ask_agent cannot reach any of them."
    elif name == "ask_agent":
        from .delegation import DEFAULT_TIMEOUT, DelegationError, ask

        try:
            result = ask(
                store,
                str(arguments.get("agent", "")).strip(),
                str(arguments.get("request", "")),
                str(arguments.get("sender") or "agent"),
                str(arguments.get("mode") or "compact"),
                int(arguments.get("timeout_seconds") or DEFAULT_TIMEOUT),
            )
        except DelegationError as error:
            raise ValueError(str(error)) from error
        text = f"Reply from {result['agent']} ({result['reply_tokens']} estimated tokens):\n\n{result['reply']}"
        if not result.get("recorded", True):
            text += (
                "\n\n[This exchange could not be written to shared memory, so the other agent "
                "will not see it later. Continuum's memory is read-only from here, usually "
                "because the agent running it sandboxes its MCP servers.]"
            )
    elif name == "list_teams":
        from .teams import PRESETS, TeamManager

        manager = TeamManager(store)
        installed = manager.list()
        lines = []
        for team in installed:
            try:
                agents = manager.load(team).get("agents", {})
            except ValueError:
                continue
            roles = ", ".join(f"{role} via {spec.get('provider')}" for role, spec in agents.items())
            lines.append(f"{team}: {roles}")
        text = "\n".join(lines) or "No teams installed in this project."
        available = [preset for preset in PRESETS if preset not in installed]
        if available:
            text += "\n\nNot installed yet: " + ", ".join(sorted(available))
            text += "\nInstall one with `continuum team init <name>`."
    elif name == "plan_workflow":
        from .orchestration import Orchestrator, OrchestrationError

        try:
            workflow = Orchestrator(store).plan(
                str(arguments.get("team", "")).strip(),
                str(arguments.get("request", "")).strip(),
                str(arguments.get("task_type")) if arguments.get("task_type") else None,
            )
        except (OrchestrationError, ValueError) as error:
            raise ValueError(str(error)) from error
        steps = "\n".join(
            f"{step['order']}. {step['role']} via {step['provider']} (task {step['task_id']})"
            for step in workflow["steps"]
        )
        text = (
            f"Planned {workflow['workflow_id']} on team {workflow['team']} "
            f"({workflow['task_type']}). Nothing has run yet.\n\n{steps}\n\n"
            "Run it with run_workflow, listing every path a writing role may edit."
        )
    elif name == "run_workflow":
        from .orchestration import Orchestrator, OrchestrationError

        allow_files = arguments.get("allow_files")
        if not isinstance(allow_files, list):
            raise ValueError(
                "allow_files is required. Pass the exact paths a writing role may edit, "
                "or an empty list to run a read-only workflow."
            )
        workflow_id = str(arguments.get("workflow_id", "")).strip()
        if not workflow_id:
            raise ValueError(
                "workflow_id is required. Call plan_workflow first and pass back the id it "
                "returned, so the plan you were shown is the one that runs."
            )
        try:
            workflow = Orchestrator(store).run_planned(
                workflow_id,
                [str(path) for path in allow_files],
                str(arguments.get("context_mode") or "compact"),
            )
        except (OrchestrationError, ValueError) as error:
            raise ValueError(str(error)) from error
        steps = "\n".join(
            f"{step['order']}. {step['role']} via {step['provider']}: {step['status']}"
            for step in workflow["steps"]
        )
        text = (
            f"{workflow['workflow_id']} finished with status {workflow['status']}.\n\n{steps}\n\n"
            "Each step's result is recorded; read it with get_agent_messages."
        )
    elif name == "get_workflow":
        workflow = store.get_workflow(str(arguments.get("workflow_id", "")).strip())
        if not workflow:
            text = "Workflow not found."
        else:
            steps = "\n".join(
                f"{step['order']}. {step['role']} via {step['provider']}: {step['status']}"
                for step in workflow["steps"]
            )
            text = (
                f"{workflow['workflow_id']} team={workflow['team']} status={workflow['status']}\n"
                f"Request: {workflow['request']}\n\n{steps}"
            )
    elif name == "get_external_sessions":
        sessions = store.list_external_sessions(20)
        text = "\n".join(
            f"{item['session_id']} {item['status']} {item['agent']} PID={item['pid']}" for item in sessions
        ) or "No attached external sessions."
    elif name == "get_external_session_context":
        session = store.get_external_session(str(arguments.get("session_id", "")))
        if not session:
            text = "External session not found."
        elif not session.get("context_path") or not Path(str(session["context_path"])).exists():
            text = "No context packet published for this external session."
        else:
            text = read_note(Path(str(session["context_path"])))
    else:
        raise ValueError(f"Unknown tool: {name}")
    return {"content": [{"type": "text", "text": text}]}


def handle_request(store: MemoryStore, request: dict[str, Any]) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None:
        return None
    try:
        if method == "initialize":
            requested = request.get("params", {}).get("protocolVersion") or PROTOCOL_VERSION
            result = {
                "protocolVersion": requested,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_definitions()}
        elif method == "tools/call":
            params = request.get("params", {})
            result = call_tool(store, str(params.get("name", "")), params.get("arguments", {}))
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except (ValueError, TypeError) as error:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": str(error)},
        }


def serve_stdio(store: MemoryStore, input_stream: IO[str] | None = None, output_stream: IO[str] | None = None) -> int:
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    if not store.config_file.exists():
        store.initialize(100_000, 0.80)
    for line in input_stream:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle_request(store, request)
        except json.JSONDecodeError:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=True) + "\n")
            output_stream.flush()
    return 0
