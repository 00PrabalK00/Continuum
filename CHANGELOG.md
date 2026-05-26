# Changelog

## 0.2.1 - Alpha

- Completed bounded semantic retrieval acceptance: refresh, event attribution,
  task/recency ranking, exact fallback and output budgets.
- Added task-bound Git worktree commands with recorded test and review gates
  before merge.
- Added explicit Control Center controls for team editing, provider tests,
  workflow plan/execute actions and compact resume packets.
- Added macOS launchd and Linux systemd user-service definitions alongside the
  existing Windows startup path.
- Added optional Docker Compose local vector-service profile without replacing
  host-local agent execution.

## 0.2.0 - Alpha

- Added opt-in sequential Continuum Teams execution with bounded per-role context packets.
- Added workflow/message persistence and MCP tools for bounded inter-agent collaboration.
- Added Ollama-ranked retrieval over stored embeddings with exact search preserved as the default.
- Added explicit CLI-agent invocation adapters without permission-bypass flags.
- Changed `continuum resume` to inject its bounded handoff as the next agent's initial prompt.
- Enforced that Ollama and OpenRouter cannot claim files through storage or MCP.
- Required claimed paths and clean unrelated state before automatically executing writer roles.

## 0.1.1 - Alpha

- Declared the release boundary: `v0.1` proves context continuity; `v0.2` owns automatic orchestration.
- Added an end-to-end multi-agent demo script and installation-first documentation.
- Added cross-platform installation smoke coverage in CI.
- Fixed file claims for hidden paths such as `.github/workflows/test.yml`.
- Made missing-Ollama diagnostic regression coverage portable across fresh CI hosts.
- Updated GitHub Actions to Node 24-compatible action major versions.
- Wait for daemon termination on Windows before returning from `continuum down` so log handles are released.
- Exclude generated example bytecode from the npm release payload.

## 0.1.0 - Alpha Baseline

- Added local project memory with SQLite events and bounded Markdown handoffs.
- Added `init`, `up`, `down`, `logs`, `handoff`, `run`, `resume`, `status`
  and `search` CLI workflows.
- Added the npm launcher front door for the host-local Python daemon.
- Added MCP stdio tools with progressive memory retrieval and raw-log access.
- Added controller tasks with status transitions and exclusive file claims through CLI and MCP.
- Added Ollama and OpenRouter as bounded model-provider backends.
- Added Continuum Teams JSON presets and routed controlled-task planning.
- Added Continuum Control Center, a local real-state developer console UI.
- Added optional Obsidian mirroring organized into one compact folder per project.
- Added Windows sign-in startup installation.
- Added deterministic `doctor` checks and expanded operational `status` reporting.
- Made Control Center read-only and provider/team initialization opt-in.
- Added five editable team starter presets and explicit planning-only workflow output.
- Added resume context estimates and npm packaging hygiene checks.
- Added the Continuum logo to README and Control Center branding, including packaged SVG delivery.
- Corrected the Control Center overview to render `latest_handoff.md` in its handoff view.
