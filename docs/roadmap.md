# Roadmap

## Implemented In v0.1

- Local project initialization and compact Markdown handoffs
- SQLite and JSONL event history
- File watching and Git working tree summaries
- Optional bounded Obsidian notes by project
- Session subprocess logging and estimated checkpoints
- Local event text search
- MCP stdio tools for current state, handoff reads, local search and handoff writes
- Persistent controlled tasks and exclusive file claims
- Ollama and OpenRouter model-provider configuration and direct bounded calls
- JSON Continuum Teams presets with deterministic routed task planning
- Continuum Control Center localhost UI for projects, providers, teams, memory, runs and handoffs
- Deterministic `continuum status` and `continuum doctor` diagnostics
- CLI-owned configuration with read-only Control Center views
- Editable team starter presets with task-only planning output
- Windows startup installation

## Next Milestones

### Semantic Retrieval

Use the stored Ollama embeddings for ranked retrieval, with bounded retrieved
context and an exact-search fallback. Users should not need a cloud account.

### Agent Adapters

Create tested adapters for interactive Codex, Claude Code and Gemini CLI
behavior on Windows, macOS and Linux.

### Orchestrator Routing

Add provider adapters and a task router that can assign sequential specialist
roles and launch workers with scoped MCP configuration. Team route planning is
implemented; automatic execution is not.

### Isolated Parallel Work

Create Git worktree-backed parallel tasks with review/test gates before any
merge. File claims alone protect routed intent; worktrees provide filesystem
isolation.

### Background Services

Provide native service installation for Windows, macOS and Linux, along with
clean status and uninstall commands.

### Optional Docker Mode

Provide a Docker Compose profile for optional vector/search and dashboard
services, without moving host file watching or local agent execution into a
required container.

## Non-Goals

- Replacing coding agents
- Automatically committing or pushing user code
- Uploading project memory to a hosted service by default
