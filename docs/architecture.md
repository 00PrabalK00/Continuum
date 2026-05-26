# Architecture

Continuum is local context continuity and sequential orchestration
infrastructure, not an unrestricted autonomous agent.

```text
agent CLI -> session recorder -> local event store -> compact handoff notes
                              \-> MCP stdio tools
                              \-> optional Obsidian project mirror
file watcher -----------------/
model provider (Ollama/OpenRouter) -> bounded memory/reasoning output
team workflow -> bounded step packet -> provider result message -> next step
```

## Controller Boundary

Continuum is the controller. Agent CLIs are workers. In `v0.2`, the controller
stores workflows, messages, tasks and exclusive file claims, and exposes them
through CLI and MCP. `team run --execute` invokes enabled providers
sequentially. Task-bound worktree create/diff/gated-merge operations are
available; automatic parallel team scheduling is not provided.

```text
task CREATED -> ASSIGNED -> RUNNING -> DONE
                         \-> BLOCKED | FAILED | NEEDS_USER
```

Only one active task may claim a path. This prevents two routed workers from
being assigned the same file through Continuum.

## Provider Boundary

Agent providers (`claude_code`, `gemini_cli`, `codex`) are intended for repo
work. Model providers (`ollama`, `openrouter`) are intentionally limited to
text or embedding work. Team validation, storage and MCP reject configurations
or claims that give model providers file-edit authority.

## Storage Boundary

Raw events and logs belong to the project and are written to `.continuum/`.
This directory should normally remain uncommitted.

The Obsidian mirror is intentionally small. It contains `Current.md`, the
latest bounded handoff and bounded session tails grouped by project identifier.
Agents can retrieve only the active project notes instead of loading every
memory.

## Agent Boundary

`continuum init` adds a small memory block to `AGENTS.md`, `CLAUDE.md` and
`GEMINI.md` without replacing existing instructions. It directs agents toward
the tiny `current.md` note first and MCP retrieval only as needed.

## Event Retrieval

Version `0.2` uses SQLite event storage and local lexical search by default.
When explicitly requested, an Ollama query embedding ranks stored embedding
previews. MCP exposes scoped reads, bounded context packets, messages and
handoff writes.

## Distribution Boundary

The npm launcher is the user-facing entrypoint and starts the bundled Python
daemon on the host. The optional Compose profile can run a local vector
service; it is not required for file watching and local agent wrapping.

## Context Checkpoints

The wrapper estimates token usage from captured output and writes a handoff at
the configured fraction of a supplied context limit. This is deliberately
described as an estimate: provider CLIs do not offer one common exact usage
interface.

`continuum resume` injects its bounded packet into a CLI launched through
Continuum. It cannot inject into unrelated sessions started directly by the
user.
