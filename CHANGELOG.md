# Changelog

## 0.1.1 - Alpha

- Declared the release boundary: `v0.1` proves context continuity; `v0.2` owns automatic orchestration.
- Added an end-to-end multi-agent demo script and installation-first documentation.
- Added cross-platform installation smoke coverage in CI.
- Fixed file claims for hidden paths such as `.github/workflows/test.yml`.
- Made missing-Ollama diagnostic regression coverage portable across fresh CI hosts.
- Updated GitHub Actions to Node 24-compatible action major versions.
- Wait for daemon termination on Windows before returning from `continuum down` so log handles are released.

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
