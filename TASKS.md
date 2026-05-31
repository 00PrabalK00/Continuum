# Tasks

## v0.1 Complete

- [x] Select the GitHub repository and package identity for the alpha.
- [x] Commit and publish the verified v0.1 baseline to GitHub.
- [x] Freeze the promise around cross-agent context continuity.

## v0.2 Implemented

- [x] [#1 Add ranked semantic retrieval over Ollama-generated embeddings.](https://github.com/00PrabalK00/Continuum/issues/1)
- [x] [#2 Add PTY-aware wrappers for Claude Code, Gemini CLI and Codex.](https://github.com/00PrabalK00/Continuum/issues/2)
- [x] [#3 Add automatic sequential Continuum Teams execution.](https://github.com/00PrabalK00/Continuum/issues/3)
- [x] [#4 Add Git worktree isolation before concurrent write workers.](https://github.com/00PrabalK00/Continuum/issues/4)
- [x] [#5 Add writable Control Center configuration and planned-task controls.](https://github.com/00PrabalK00/Continuum/issues/5)
- [x] [#6 Add macOS and Linux service installers.](https://github.com/00PrabalK00/Continuum/issues/6)
- [x] [#7 Add optional Docker Compose mode.](https://github.com/00PrabalK00/Continuum/issues/7)

## v0.3 Implemented

- [x] Add claim recovery commands with auditable reasons (`continuum claim list/release/recover --stale`).
- [x] Add persisted retry/continue for a failed sequential workflow step without rebuilding earlier tasks (`continuum workflow list/show/retry`).

## Remaining Roadmap

- [ ] Improve wrapper review output capture when a provider returns a plan-only completion marker.

## Product Strategy Backlog

- [x] Ship `continuum objective` as the first one-command planning primitive for [#36](https://github.com/00PrabalK00/Continuum/issues/36).
- [x] Ship `continuum evidence` and `continuum pr-packet` as the first evidence-pack and PR-packet primitives.
- [x] Ship `continuum context enrich`, `context diff` and `context score` as first context-intelligence primitives for [#31](https://github.com/00PrabalK00/Continuum/issues/31) and [#32](https://github.com/00PrabalK00/Continuum/issues/32).
- [x] Ship policy, command risk, secret scanning, audit export and MCP trust controls for [#33](https://github.com/00PrabalK00/Continuum/issues/33).
- [x] [#27 Promote evidence/events/session logs into Agent Flight Recorder run records.](https://github.com/00PrabalK00/Continuum/issues/27)
- [x] [#28 Build the Control Center Workflow Timeline MVP.](https://github.com/00PrabalK00/Continuum/issues/28)
- [x] [#29 Build the Multi-Agent Worktree Board.](https://github.com/00PrabalK00/Continuum/issues/29)
- [x] [#30 Build the Context Packet Studio UI.](https://github.com/00PrabalK00/Continuum/issues/30)
- [x] [#34 Add cost-aware routing and Agent ROI evidence.](https://github.com/00PrabalK00/Continuum/issues/34)
- [x] [#35 Add the with/without Continuum benchmark harness.](https://github.com/00PrabalK00/Continuum/issues/35)

## Release Issues

- [ ] [#8 Publish the npm package and verify the npx first-run flow.](https://github.com/00PrabalK00/Continuum/issues/8)
- [ ] [#9 Publish the Python package to PyPI.](https://github.com/00PrabalK00/Continuum/issues/9)
- [ ] [#10 Record the v0.1 cross-agent context continuity demo video.](https://github.com/00PrabalK00/Continuum/issues/10)
