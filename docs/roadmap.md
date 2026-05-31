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

### Workflow Timeline And Agent Scheduling

Add a visual workflow layer that shows multi-agent execution over time without
claiming autonomous behavior that the scheduler has not performed. The view
should combine a task timeline, dependency graph, agent lanes, context packet
handoffs and file ownership history.

The timeline should show:

- Which model, agent or tool is assigned to each task.
- When a task started, ended, blocked or entered user approval.
- Which tasks depend on other tasks.
- Which agent handed context to another agent.
- Which files were changed or claimed by each agent.
- Which memory packets were created, reused or superseded.
- Which agents are running in parallel worktrees.
- Which agent produced the final output.

This should be backed by structured Continuum events, workflow messages,
tasks, claims, context packets and worktree state. The UI should render this
state as a read-and-control surface; it must not invent fake execution,
provider costs, productivity scores or hidden autonomous activity.

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

## Product Strategy Backlog

See [Product Strategy](product-strategy.md) for the market framing and product
direction behind these issues.

Already shipped on the current branch:

- `continuum objective` for coordinated objective planning.
- `continuum evidence` and `continuum pr-packet` for trust artifacts.
- `continuum context enrich`, `continuum context diff` and `continuum context
  score` for inspectable context routing.
- `continuum policy`, `continuum command classify`, `continuum secrets scan`,
  `continuum audit export` and `continuum mcp trust` for governance controls.

Phase 1 should make the core workflow undeniable:

- [x] [#27 Agent Flight Recorder](https://github.com/00PrabalK00/Continuum/issues/27):
  promote evidence, events and session logs into replayable run records.
- [x] [#28 Workflow Timeline MVP](https://github.com/00PrabalK00/Continuum/issues/28):
  add the Control Center timeline over real scheduling state.
- [x] [#29 Multi-Agent Worktree Board](https://github.com/00PrabalK00/Continuum/issues/29):
  add the screenshot-ready lane board for isolated worktrees.
- [x] [#30 Context Packet Studio](https://github.com/00PrabalK00/Continuum/issues/30):
  add UI inspection and comparison for context packets.
- [#36 `continuum objective` one-command demo flow](https://github.com/00PrabalK00/Continuum/issues/36):
  harden the shipped planner into the main demo workflow.

Phase 2 should make context routing smarter:

- [#31 Symbol-aware context builder](https://github.com/00PrabalK00/Continuum/issues/31):
  deepen current context enrichment into automatic packet construction.
- [#32 Context diff and context score](https://github.com/00PrabalK00/Continuum/issues/32):
  persist current diff and score output as first-class packet metadata.

Phase 3 should productize governance and safety:

- [#33 MCP trust registry](https://github.com/00PrabalK00/Continuum/issues/33):
  harden the shipped registry and gate more MCP boundaries through it.

Phase 4 should prove cost and quality:

- [x] [#34 Cost-aware model routing and Agent ROI evidence](https://github.com/00PrabalK00/Continuum/issues/34)
- [x] [#35 Benchmark harness with and without Continuum](https://github.com/00PrabalK00/Continuum/issues/35)

## Non-Goals

- Replacing coding agents
- Automatically committing or pushing user code
- Uploading project memory to a hosted service by default
