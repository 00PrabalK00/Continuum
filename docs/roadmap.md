# Roadmap

## Release Boundary

```text
v0.1 proves Continuum can preserve context across agents.
v0.2 proves Continuum can automatically orchestrate agents.
```

## Implemented Through v0.4.0

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
- Explicit Control Center team/provider/workflow actions with CLI parity
- Editable team starter presets with task-only planning output
- Shared packaged logo asset and verified Control Center handoff display
- Windows startup installation
- Opt-in sequential Continuum Teams execution through enabled provider adapters
- Bounded role context packets, persisted result messages and MCP workflow tools
- Ollama-ranked retrieval over stored embedding previews
- Prompt injection for sessions launched through `continuum resume`
- File claim enforcement for model providers at storage and MCP boundaries
- Task worktrees with diff metadata and required test/review gates
- Optional Docker Compose vector-service profile
- Interactive `continuum shell` control surface with color-coded targets,
  optional motion and project-scoped slash commands
- Explicit live interactive agent sessions through `continuum run --interactive`
  and `continuum resume --interactive`, backed by PTY on macOS/Linux and
  `pywinpty` on Windows
- Safe external-session bridging for manually launched Claude Code, Codex and
  Gemini CLI processes through detection, tracked attachment, bounded context
  packets and MCP retrieval

## v0.2 Issues

### [#2 PTY-Aware Agent Wrappers](https://github.com/00PrabalK00/Continuum/issues/2)

The cross-platform terminal base layer shipped in v0.3.0. Create dedicated
adapters for interactive Codex, Claude Code and Gemini CLI behavior on
Windows, macOS and Linux.

Remaining adapter work must normalize provider-specific prompts, approvals,
cancellation and recovery above the live stdin/output, interrupt, resize and
PTY/ConPTY primitives now available.

### External CLI Session Integration

Detection, tracked attachment and cooperative bounded context publication ship
in v0.4.0. Further provider-native delivery can be added only where the
provider exposes a controlled, auditable session API; Continuum must not
silently manipulate arbitrary terminal processes.

### Agent-Specific Terminal Adapters

Build dedicated adapters after the PTY base layer is validated. Adapters must
own prompt injection, streamed output normalization, cancellation, prompt or
approval handling and failure recording for each supported CLI.

### Parallel Team Scheduling

Extend the existing gated worktree primitives into automatic parallel
scheduling only after task dependency, file ownership, review and merge-risk
rules are deterministic. Each writer requires a separate worktree and the
same explicit test/review merge gates used today.

### Sequential Recovery

Add an auditable `team continue` path that resumes a failed sequential
workflow from its stopped step without silently repeating completed writer
steps.

### Background Services

Provide native service installation for Windows, macOS and Linux, along with
clean status and uninstall commands.

Tracked as [#6 macOS and Linux service installers](https://github.com/00PrabalK00/Continuum/issues/6).

## Release Issues

- [#8 Publish npm package and verify npx first-run flow](https://github.com/00PrabalK00/Continuum/issues/8)
- [#9 Publish the Python package to PyPI](https://github.com/00PrabalK00/Continuum/issues/9)
- [#10 Record the v0.1 context continuity demo video](https://github.com/00PrabalK00/Continuum/issues/10)

## Non-Goals

- Replacing coding agents
- Automatically committing or pushing user code
- Uploading project memory to a hosted service by default
