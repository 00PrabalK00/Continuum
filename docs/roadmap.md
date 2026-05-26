# Roadmap

## Release Boundary

```text
v0.1 proves Continuum can preserve context across agents.
v0.2 proves Continuum can automatically orchestrate agents.
```

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
- Shared packaged logo asset and verified Control Center handoff display
- Windows startup installation

## v0.2 Issues

### [#1 Semantic Retrieval Using Ollama Embeddings](https://github.com/00PrabalK00/Continuum/issues/1)

Use the stored Ollama embeddings for ranked retrieval, with bounded retrieved
context and an exact-search fallback. Users should not need a cloud account.

### [#2 PTY-Aware Agent Wrappers](https://github.com/00PrabalK00/Continuum/issues/2)

Create tested adapters for interactive Codex, Claude Code and Gemini CLI
behavior on Windows, macOS and Linux.

### [#3 Automatic Continuum Teams Execution](https://github.com/00PrabalK00/Continuum/issues/3)

Add provider adapters and a task router that can assign sequential specialist
roles and launch workers with scoped MCP configuration. Team route planning is
implemented; automatic execution is not.

### [#4 Isolated Parallel Work](https://github.com/00PrabalK00/Continuum/issues/4)

Create Git worktree-backed parallel tasks with review/test gates before any
merge. File claims alone protect routed intent; worktrees provide filesystem
isolation.

### Background Services

Provide native service installation for Windows, macOS and Linux, along with
clean status and uninstall commands.

Tracked as [#6 macOS and Linux service installers](https://github.com/00PrabalK00/Continuum/issues/6).

### [#5 Writable Control Center Configuration](https://github.com/00PrabalK00/Continuum/issues/5)

Add team editing, provider testing and planned-task run/resume controls without
decorative or simulated activity.

### [#7 Optional Docker Mode](https://github.com/00PrabalK00/Continuum/issues/7)

Provide a Docker Compose profile for optional vector/search and dashboard
services, without moving host file watching or local agent execution into a
required container.

## Release Issues

- [#8 Publish npm package and verify npx first-run flow](https://github.com/00PrabalK00/Continuum/issues/8)
- [#9 Publish the Python package to PyPI](https://github.com/00PrabalK00/Continuum/issues/9)
- [#10 Record the v0.1 context continuity demo video](https://github.com/00PrabalK00/Continuum/issues/10)

## Non-Goals

- Replacing coding agents
- Automatically committing or pushing user code
- Uploading project memory to a hosted service by default
