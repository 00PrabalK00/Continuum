# Changelog

## 0.13.0

Continuum's history became something you can inspect, and its published
numbers became something you can trace to a run.

- `continuum log`, `diff`, `blame` and `restore`. Checkpoints can be listed,
  compared, attributed and returned to. Restoring appends a checkpoint carrying
  the old state forward rather than rewriting the log, the way `git revert`
  does and `git reset` does not.
- Checkpoints record the commit they were written against, and every read
  reports the drift. Continuum's own context had spent five merged pull
  requests telling each agent to review one that had already merged.
- `continuum branch` and `continuum merge`. Two agents on one project used to
  share a single line of context, so the newer save silently erased the older.
  Each agent can have its own branch, and a merge where both sides changed the
  same thing stops and prints both claims rather than letting the most recent
  write win.
- Benchmark accuracy is re-measured and published: 100% (98 to 100) with
  context injected, 17% to 20% without, 30 trials per cell across two agents.
  Every figure on docs/benchmarks.md and in the README is generated from
  benchmarks/results.json rather than typed.
- Confidence intervals are Wilson score rather than a percentile bootstrap. The
  bootstrap could only resample the values it was given, so a cell where every
  trial scored the same printed "100 to 100" and asserted no uncertainty at all
  from 30 observations.
- Six instrument faults are documented on the benchmark page, including two
  found during this release: the delegation arm scored every trial zero without
  reading the reply, and a machine that suspended mid-run charged the whole
  suspension to one trial.
- The README is rewritten around the results, and its install command now names
  the right package. It said `continuum-memory`, which is a different project
  already on PyPI.
- Added cross-session quota awareness. Continuum records what each agent
  consumed per session in a new `agent_usage` table, and records verbatim any
  message an agent printed when it ran out. `continuum limits` and the
  `get_agent_limits` MCP tool report both, and `continuum go` prefers an agent
  that has not reported running out.
- Nothing invented is displayed. There is no API for remaining quota and the
  CLIs say nothing until they refuse, so no percentage, gauge or remaining
  balance appears anywhere. What an agent said is quoted as it wrote it, what
  Continuum counted is labelled an estimate and described as a floor, and
  remaining quota is stated as unknowable.
- A phrase like "usage limit reached" appears in ordinary output, including in
  Continuum's own source, so a single occurrence is recorded but never acted on.
  A signal only affects which agent runs next when the agent also stated a reset
  time, repeated itself, or the session then failed.
- Fixed two token-counting bugs this surfaced. Interactive sessions counted ANSI
  escape sequences and full TUI repaints as consumption, so the context
  checkpoint fired far too early there; output is now cleaned before it is
  measured. The injected prompt was estimated for its event and then dropped, so
  the session total omitted the whole handoff.

- Prepared both packages for release. `pyproject.toml` gains project URLs, a
  PEP 639 license declaration and per-version classifiers; `package.json` gains
  repository, homepage, bugs and author. The README logo is now an absolute URL,
  since PyPI does not resolve repository-relative paths and would have rendered
  it as a broken image.
- Added a tag-driven release workflow that runs the full test matrix again,
  refuses to publish if the tag disagrees with any of the three files carrying
  the version, and publishes to PyPI by trusted publishing rather than a stored
  token. `docs/releasing.md` records the one-time account setup that cannot be
  done from CI.
- Nothing is published yet, so the install instructions still describe cloning
  the repository. They change when a release has actually been accepted and
  verified, not before.

- Added the `save_progress` MCP tool, so an agent can record where work has got
  to on its own initiative. It fills in whatever the agent does not supply,
  asking the configured handoff model for a summary and falling back to the
  recorded task, so "save my progress" needs no arguments. The agent
  instructions written by `continuum install` now say when to call it: when the
  user asks, when something worth keeping is finished, and when the agent
  notices it is running low on context. Only the agent can see how much context
  it has left, so that judgement is stated as belonging to the agent rather than
  to Continuum.
- Fixed a compounding annotation on every save path except one. A carried
  forward next step is marked unconfirmed when a session changed files, and only
  the session-end path recorded the original text to rebuild from. A
  `continuum save` or an MCP handoff in between promoted the annotated text to
  the base, so the next session annotated the annotation, and it grew on every
  cycle. All save paths now go through one function that carries the base.
- The MCP server no longer hardcodes its context limit and threshold, which
  diverged from the defaults every other entry point uses.

## 0.12.0 - Alpha

Search that understands the question, workflows agents can drive, and a setup
that asks instead of assuming.

- `search_memory`, the tool agents actually call, ranked results with a SQL
  `LIKE` over the payload. It now uses SQLite's FTS5 with BM25 scoring, so
  "payment rename" finds "renamed the payment client" and results come back in
  a useful order. FTS5 is part of the standard library, so this needs no model,
  no service and no download, and the index is built incrementally.
- Embeddings are now an optional second tier rather than the only semantic
  option. When a project has them and the local model answers, semantic hits
  merge ahead of the lexical ones; when it does not, search reports that it
  matched on wording and carries on. Continuum's dependencies are unchanged.
- Added `list_teams`, `plan_workflow`, `run_workflow` and `get_workflow` to the
  MCP server. An agent can now plan a multi-agent workflow and run it, which
  previously existed only as a CLI command. Planning executes nothing.
  Running requires an explicit list of files the writing roles may edit, the
  same gate `continuum team run --execute` enforces.
- Search fuses the ranked text and meaning results by reciprocal rank instead
  of listing one source before the other. Taking the semantic list first filled
  every slot with its full quota of candidates whatever their similarity, so an
  exact wording match could disappear entirely and enabling embeddings could
  make search worse than leaving them off.
- `run_workflow` runs the workflow `plan_workflow` returned, by id. It called
  `execute`, which plans again, so following the documented flow left the
  planned workflow and its tasks permanently PLANNED while a second workflow
  ran.
- Handoffs are embedded as they are recorded, so meaning-based search stays
  level with the log rather than reflecting only what existed at setup. Setup
  now indexes every recorded handoff rather than those inside a 200-event
  window, and skips ones already indexed.
- Ollama summaries are offered only when a chat model is actually downloaded,
  and the model found is what gets recorded. A fresh Ollama installed for search
  may hold only the embedding model, in which case every summary attempt failed
  and fell back while setup reported success.
- When Ollama is missing, `continuum install` offers to install it through the
  platform's package manager, printing the exact command before asking so
  agreeing means agreeing to something specific. The default answer is no, and
  nothing is installed without an explicit yes. Where no package manager is
  available it prints the download address instead.
- `continuum install` asks what else you want when a terminal is attached: it
  can start a local Ollama, download the embedding model, index what you have
  already recorded, and pick a model to write session summaries. It never
  installs Ollama itself, and prints where to get it instead. `--yes` skips the
  questions, which is also the behaviour with no terminal attached.

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
- Added cross-agent delegation. `continuum/delegation.py` runs one agent CLI
  from inside another with the same bounded project context and returns its
  reply, and the MCP server exposes it as `ask_agent` plus `list_agents`. An
  agent connected over MCP can now consult any other installed agent —
  Claude Code asking Codex, Codex asking Claude, in any direction — and both
  halves of the exchange are recorded in shared memory. `continuum ask <agent>
  <request>` does the same from a terminal.
- Agent specs gained `oneshot_args` for the flags a CLI needs to answer a
  single prompt non-interactively (`claude -p`), settable with
  `continuum agent add --oneshot-arg`.
- Delegation kills the agent's whole process tree on timeout. Killing only the
  direct child left shim grandchildren holding the output pipes open, so a
  stalled agent hung the caller indefinitely instead of timing out.
- An agent that stops on a sign-in or approval question is now reported as
  such, with the question it asked, instead of as an unexplained timeout.
- `continuum setup` now registers the Continuum MCP server for Codex and
  Gemini as well as Claude Code, writing project-local `.codex/config.toml`
  and `.gemini/settings.json` entries instead of printing snippets to paste.
  Existing settings are merged, not replaced, and re-running is a no-op.
- The first `continuum go <agent>` connects that agent to the MCP server, so
  cross-agent delegation needs no setup step. `continuum setup` remains as a
  way to connect every installed agent at once.
- `continuum --help` is down to the two daily commands plus `help`.
- Fixed re-initialization discarding recorded work. `continuum init`, and the
  auto-initialization inside `setup`, overwrote `latest_handoff.md` and
  `current.md` with the starter text and replaced `config.json` wholesale,
  dropping the handoff model and connected-agent settings. The status card
  reads the event log rather than those files, so the loss was invisible until
  an agent was handed the reset context. Initialization now seeds a handoff
  only when none exists and merges config instead of replacing it.
- Delegation survives a read-only memory store. An agent that sandboxes its MCP
  servers gives Continuum a store it cannot write to; the exchange now goes
  ahead and the reply notes that it was not recorded, instead of failing.
- A launch refused by the calling agent's sandbox is reported as such, with the
  flag that relaxes it, rather than as a bare permission error.
- Added `continuum install`: detects the AI coding agents on the machine and
  sets Continuum up inside each one in that agent's native format — MCP server,
  Claude Code skill and session hooks, Cursor/Windsurf/Cline rule files, and
  `AGENTS.md` for anything else. Idempotent, previewable with `--dry-run`, and
  scopeable with `--only`.
- Added `continuum hook session-start` and `continuum hook session-end`, the
  entry points those hooks call. With them installed, a Claude Code session
  begins with the project context already loaded and records a handoff when it
  ends, so Continuum needs no commands during normal work.
- The exit handoff is derived from the session rather than repeating the last
  one. Without a handoff model there is nothing to summarize the work in prose,
  but the files a session changed are still recorded, and the previous next step
  is carried forward marked unconfirmed instead of restated as if fresh. The
  annotation is rebuilt from the original each time, so it does not stack up,
  and Continuum's own scaffolding files are excluded so the user's edits are
  what show.
- A session that kept working past its context checkpoint, or that then failed,
  now records a handoff at exit. Previously the checkpoint always won, so
  everything after it — including a failure — was missing from what the next
  agent received.
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
