# MCP Integration

Continuum includes a stdio MCP server scoped to one initialized project.

```bash
continuum mcp serve --project /path/to/my-project
```

## Tools

| Tool | Purpose |
| --- | --- |
| `get_startup_context` | Read the smallest current-task note first |
| `get_current_state` | Read compact active project state when needed |
| `get_latest_handoff` | Read compact continuation context |
| `search_memory` | Search local events and receive memory IDs |
| `expand_memory` | Expand a selected memory ID |
| `get_raw_log` | Read a bounded raw session log only for debugging |
| `write_handoff` | Store the task and exact next action |
| `get_open_tasks` | List active routed work |
| `get_context_packet` | Read bounded role context and applicable messages |
| `get_workflows` | Inspect recent planned/executed workflow state |
| `post_agent_message` | Store a bounded result or instruction for a role |
| `get_agent_messages` | Read messages addressed to a role |
| `claim_task_files` | Exclusively reserve files for one worker task |
| `complete_task` | Finish a task and release its claims |

## Client Configuration

Use this shape in an MCP-capable client:

```json
{
  "command": "continuum",
  "args": ["mcp", "serve", "--project", "/path/to/my-project"]
}
```

The MCP process writes only protocol messages to standard output. Project logs
and handoffs remain in `.continuum/` and the configured Obsidian folder.
