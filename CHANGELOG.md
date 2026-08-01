# Changelog

## 0.11.0 - Alpha

Agent-agnostic release: the daily loop is one command, and Continuum is no
longer tied to a fixed set of agent CLIs.

- Wrapped sessions now write a continuation handoff when the agent exits, so
  the manual save is no longer part of the loop. A context-limit checkpoint
  earlier in the same session is kept rather than summarized twice; a non-zero
  exit is recorded as the next action.
- `continuum go` no longer requires an agent name. With none given it picks an
  installed agent other than the one that just ran — the common case after a
  context limit — and prints which it chose and why.
- Added `continuum/agents.py`, a launch registry. Any command-line agent on
  PATH works with `continuum go <name>`; unrecognized names are adopted with
  the trailing-argument convention and remembered in `.continuum/agents.json`.
  `claude`, `codex` and `gemini` keep their tuned specs.
- Added `continuum agent add|list|remove` for CLIs that take their prompt
  another way: `--inject arg|subcommand|flag|stdin`.
- Interactive sessions fall back to a generic terminal adapter for agents
  without a tuned one, instead of refusing to launch.
- Removed the fixed agent choice lists from `go`, `run`, `resume`, `chat`,
  `task assign`, `task claim`, `worktree resume` and `shell`.
- `continuum --help` now lists only the daily commands. Every other command
  still runs unchanged and is listed by the new `continuum help --all`.
- Fixed silent context truncation on Windows. Agents that resolve to a
  `.cmd`/`.bat` shim run through cmd.exe, which ends the command line at the
  first newline, so the injected handoff arrived as its first line only —
  `gemini` from npm is such a shim. Those agents now receive a one-line
  pointer to `.continuum/latest_handoff.md` and read the full handoff
  themselves. Agents using stdin injection are unaffected and keep the
  inline context.

## 0.10.0 - Alpha

Simple front door release: the daily handoff loop is now three flag-free
commands with zero required setup.

- Added `continuum save "<task> | <next step>"` — plain-words handoff with no
  flags; auto-initializes the project on first use.
- Added `continuum go <agent> [mode]` — open claude/codex/gemini with the saved
  context already injected; interactive by default in a TTY, auto-creates a
  starter handoff when none exists.
- Added `continuum copy [mode]` — print the paste-ready context and copy it to
  the system clipboard for use with any AI chat, including web UIs.
- Added `continuum setup` — one-shot initialization that detects installed
  agent CLIs, registers the Continuum MCP server with Claude Code and prints
  config snippets for Codex and Gemini.
- Bare `continuum` now prints a compact status card (task, next step, save
  age, daily commands) instead of an argparse error.
- Added `continuum handoff-llm set|show|off` — configure a dedicated third LLM
  (ollama or openrouter, any model) used only for context creation and
  handoff writing. When set, bare `continuum save` summarizes recorded session
  material into a task/next-step pair, and the context-limit checkpoint writes
  a real continuation handoff from the session tail instead of a synthetic
  placeholder. Provider failures always fall back to recorded-state handoffs;
  hosted calls go through the existing secret-scrubbing egress path.

## 0.9.0 - Alpha

Trust UI release: surface the evidence Continuum already records and add
cost/benefit accounting on top of it.

- Added `continuum flight-record <task>` — a replayable Agent Flight Recorder
  record built from stored state (context packet, claimed vs touched files,
  gate evidence, events, messages and risks), never from agent self-reports.
- Added `continuum roi` — Agent ROI evidence: tokens, cost-per-accepted-change,
  out-of-scope edits, reruns, manual corrections, provider usage and
  deterministic routing recommendations.
- Added `continuum benchmark capture`/`compare` — a with-vs-without harness that
  diffs task metrics and reports an evidence-based verdict.
- Added Control Center trust views: Workflow Timeline, Multi-Agent Worktree
  Board, Agent Flight Recorder, Agent ROI and Context Packet Studio, backed by
  new read-only `/api/flight-records`, `/api/flight-record`, `/api/timeline`,
  `/api/worktree-board`, `/api/context-packets` and `/api/roi` endpoints.
- Extended `store.messages` to carry workflow and task references so flight
  records and the timeline can attribute messages to their task.

## 0.8.0 - Alpha

Trust-layer release: make Continuum verify, not trust, what agents claim.

- Added `continuum evidence <task>` — an inspectable evidence pack (claimed vs
  changed files, commands, test/review gates with their commit SHAs, and
  deterministic risk flags for out-of-scope edits or post-gate changes).
- Added `continuum pr-packet <task>` — a reviewer-facing markdown packet with
  changed files, agent contributions, test and review evidence, known risks
  and rollback notes.
- Added a governance policy file `.continuum/policy.json` (`continuum policy
  init`/`show`) enforcing denied files, allowed providers and sensitive globs,
  with audit events on violations.
- Added a pre-egress secret scrubber: context packets, delegation packets,
  external-session context and hosted-provider (`model ask`) prompts are
  redacted before leaving the machine; `continuum secrets scan <path>` checks
  files or stdin. Local Ollama traffic is unaffected.
- Added claim recovery: `continuum claim list`, `claim release --reason` and
  `claim recover --stale` to release orphaned file claims with an audit trail.
- Added workflow retry/continue: `continuum workflow list`/`show`/`retry`
  resumes a failed sequential workflow from the failed step without recreating
  completed tasks.
- Fixed the Windows autostart filename to be project-scoped so installing or
  removing the service for one project no longer clobbers another's entry.
- Fixed `continuum init` to write `.continuum/.gitignore` so machine-local
  state never dirties the project tree or blocks `worktree merge`.
- Hardened `worktree merge` to remove the merged worktree and delete its
  branch, made `continuum doctor` exit zero when only the daemon is stopped,
  and added a per-session token guarding Control Center mutation endpoints.
- Reconciled the roadmap: closed PTY-aware wrappers (#2) and macOS/Linux
  service installers (#6); documentation corrected (`current_state.md`,
  starter preset list, packaged PNG asset).

## 0.7.3 - Alpha

- Added `continuum chat` for one-shot messages to Claude, Codex or Gemini with
  bounded Continuum context injected.
- Made plain text in `continuum shell` dispatch to the selected agent as a
  compact-context chat instead of requiring every message to be a slash command.
- Kept slash commands as the control surface for actions like `/switch`,
  `/resume-terminal`, `/memory` and `/handoff`.

## 0.7.2 - Alpha

- Made `model ask` accept unquoted multi-word prompts for pasted text.
- Added provider-specific fix commands for unreachable Ollama and OpenRouter
  HTTP 401 failures.
- Added bracketed paste ingestion with compact `{n chars}` receipts in the
  interactive shell while preserving the full pasted command text.

## 0.7.1 - Alpha

- Added `/switch <agent> [mode]` in `continuum shell` to change agents and
  immediately resume with bounded Continuum context injected.
- Made exact CLI-style slash commands dispatchable from the shell, including
  `/mcp serve`, `/adapters list`, `/context build ...`, `/handoff --task ...`
  and `/instruct --planner ...`.
- Documented that slash commands automatically inject the active project unless
  the user supplies `--project`.

## 0.7.0 - Alpha

- Added Hierarchical Model Delegation through `continuum instruct`.
- Added graph-backed delegation state and compact markdown execution packets.
- Added slash-command support for `/instruct planner=... executor=... goal="..."`.

## 0.6.3 - Alpha

- Added optional Docker UI deployment support.
- Fixed the Docker image build by copying `README.md` before `pip install .`,
  matching the package metadata declared in `pyproject.toml`.
- Added contributor credit for the Docker support PR.

## 0.6.2 - Alpha

- Fixed the PTY input receipt acceptance test on macOS by comparing resolved
  working directories instead of raw `/var` and `/private/var` path strings.

## 0.6.1 - Alpha

- Added a PTY/ConPTY acceptance test that proves scripted input was consumed
  by the intended live interactive process.
- The receipt checks target id, cwd, process generation, prompt/input hash,
  PTY acceptance, transcript observation and output advancement.

## 0.6.0 - Alpha

- Added deterministic parallel worktree scheduling for large objectives.
- Added lane ownership, dependency tracking, context packets and schedule
  status for isolated multi-agent work.
- Added `continuum worktree resume` so agents run inside their assigned
  worktree while receiving shared root Continuum memory.

## 0.5.0 - Alpha

- Added dedicated Claude Code, Codex and Gemini CLI interactive terminal
  adapters above the existing PTY/ConPTY transport.
- Added provider-specific bounded context injection, Codex project scoping and
  persisted visible terminal phase/approval tracking.
- Added `continuum adapters list` so users can inspect supported behavior and
  the explicit no-auto-approval safety boundary.

## 0.4.0 - Alpha

- Added cross-platform detection and explicit bridging of manually launched
  Claude Code, Codex and Gemini CLI processes.
- Added daemon auto-registration for matching project sessions plus bounded
  external context packets retrievable through MCP.
- Added persistent external session identity/liveness tracking and explicit
  project-binding safeguards.

## 0.3.0 - Alpha

- Added real interactive `run` and `resume` sessions backed by PTY on
  macOS/Linux and `pywinpty` on Windows.
- Added `/terminal` and `/resume-terminal` slash routes while preserving
  captured-output session commands.
- Preserved session logging, token checkpoint estimation and compact handoff
  generation for interactive sessions.

## 0.2.3 - Alpha

- Replaced the prior SVG mark with the supplied navy-and-teal transparent PNG
  logo in README and Control Center branding.

## 0.2.2 - Alpha

- Added `continuum shell`, a project-scoped interactive slash-command console.
- Added color-coded Claude, Codex, Gemini, Ollama and OpenRouter terminal/provider
  labels with per-session color and motion toggles.
- Added slash aliases for health, handoff/resume, memory, providers, teams,
  controlled tasks, worktrees, services, MCP guidance and Control Center.
- Kept terminal capability claims explicit: wrapped sessions remain non-PTY
  until platform-specific terminal adapters are implemented.

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
- Pinned worktree test and review gates to the merge candidate commit and
  rejected unsafe team configuration filenames in Control Center.

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
