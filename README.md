<p align="center">
  <img src="continuum/ui/logo.png" alt="Continuum logo" width="240">
</p>

# Continuum

Local shared memory and controlled workflows for AI coding agents.

## Requirements

- Python 3.9 or newer.
- Git for task worktrees.
- One or more optional agent CLIs: `claude`, `codex`, `gemini`.
- Optional: Ollama for local embeddings and summaries.
- Optional: an OpenRouter API key for hosted model calls.
- Optional: an Obsidian vault folder for readable mirrored handoffs.

## Install

From the repository:

```bash
git clone https://github.com/00PrabalK00/Continuum.git
cd Continuum
python -m pip install -e .
continuum --version
```

Run through the npm front door from GitHub without installing globally:

```bash
npx -y github:00PrabalK00/Continuum init
npx -y github:00PrabalK00/Continuum doctor
npx -y github:00PrabalK00/Continuum up
npx -y github:00PrabalK00/Continuum ui --open
```

## Daily Use: One Command

Work through Continuum and it records the handoff for you. There is no setup
step and nothing to type before you stop:

```bash
continuum go
```

That opens an agent with your previous context already injected, records the
session, and writes a fresh continuation handoff when the agent exits. When
the agent runs out of context, run it again — Continuum hands the same work to
the next available agent:

```bash
continuum go            # picks an agent you did not just use
continuum go codex      # or name one explicitly
```

For AI that has no CLI — web ChatGPT, claude.ai, anything else — copy the same
context to your clipboard instead:

```bash
continuum copy
```

Running bare `continuum` shows where you left off:

```text
Continuum - my-project
Task: fixed the auth bug
Next: test the retry logic
Saved: 2 hours ago
```

### Any agent CLI

`continuum go <name>` works with any command-line agent on your PATH, not a
fixed list. An unrecognized name is adopted on first use with the convention
most CLIs accept — the context arrives as the final argument:

```bash
continuum go hermes
continuum go opencode
```

Agents that take their prompt another way can be described once:

```bash
continuum agent add myagent --inject flag --flag --task        # myagent --task "<context>"
continuum agent add otheragent --inject subcommand --subcommand run  # otheragent run "<context>"
continuum agent add piped --inject stdin                       # context written to stdin
continuum agent list
```

`claude`, `codex` and `gemini` ship with tuned specs, so they need no
registration.

### Optional extras

Save an explicit handoff yourself at any point — useful mid-session, or after
work done outside a Continuum-launched agent:

```bash
continuum save "fixed the auth bug | next: test the retry logic"
```

Connect installed agent CLIs over MCP so they can query memory themselves:

```bash
continuum setup
```

Configure a third, dedicated LLM to write the handoffs. When set, session
exits, context-limit checkpoints and bare `continuum save` all get a real
written summary instead of recorded state. Any failure falls back:

```bash
continuum handoff-llm set ollama llama3.1:8b          # local
continuum handoff-llm set openrouter openai/gpt-4o-mini  # hosted
continuum handoff-llm show
continuum handoff-llm off
```

Everything below this point is the advanced surface: daemons, teams,
worktrees, governance and evidence. None of it is required for the daily
loop, and none of it appears in `continuum --help` — list it with
`continuum help --all`.

## Initialize A Project

Run these commands inside the project whose context you want Continuum to
preserve:

```bash
continuum init
continuum doctor
continuum up
continuum status
```

Mirror compact notes into Obsidian:

```bash
continuum init --vault "/path/to/your/Obsidian Vault/Agents"
```

Continuum creates project-local state in `.continuum/`. Keep that directory
out of source control.

## Set Up Agents

Connect an agent CLI to Continuum's MCP server for bounded context:

```bash
# Claude Code (project-scoped)
claude mcp add continuum -- continuum mcp serve --project .

# Gemini CLI (project-scoped)
# Add to .gemini/settings.json:
# { "mcpServers": { "continuum": { "command": "continuum", "args": ["mcp", "serve", "--project", "."] } } }

# Codex (project-scoped)
# Add to .codex/config.toml:
# [mcpServers.continuum]
# command = "continuum"
# args = ["mcp", "serve", "--project", "."]
```

Verify with `continuum doctor`. See [MCP Setup](docs/mcp-setup.md) for
details.

## Open The Interactive CLI

```bash
continuum shell
continuum shell --agent gemini --color always --animation on
```

The shell uses slash commands and automatically scopes actions to the current
project:

```text
/status
/doctor
/terminals
/agent claude
/switch gemini normal
/chat claude hi
/handoff Fix authentication retry | Run the failing API test.
/resume codex compact
/memory authentication callback --semantic
/context build coder --mode compact
/mcp serve
/team run default_dev_team "Fix failing auth test"
/instruct planner=claude-opus-4-1-20250805 executor=codex mode=checkpoint goal="Fix auth test"
/worktree list
/color off
/motion off
/quit
```

Agent targets have distinct terminal colors in the shell. Color and short
action animations can be disabled per session. Use `/terminal` or
`/resume-terminal` for live PTY/ConPTY sessions. Any current `continuum`
command can also be run as a slash command, for example `/status --events`,
`/run --interactive codex` or `/task list`; the shell injects the current
project automatically unless you pass `--project` yourself. `/switch <agent>
[mode]` changes the active agent target and immediately resumes it with the
latest compact context. Bracketed paste input is stored in full and displayed
as a compact `{n chars}` receipt before the command runs.

Plain text in the shell is sent to the selected agent with compact Continuum
context. These are equivalent:

```text
hi
/chat claude hi
```

## Record And Resume Agent Work

Run an agent through Continuum:

```bash
continuum run codex
continuum run claude
continuum run gemini
```

Run an agent in a live interactive terminal for prompts, full-screen tools,
interrupts and streamed input/output:

```bash
continuum adapters list
continuum run --interactive codex
continuum resume --interactive claude compact
```

On Windows, Python installs `pywinpty` with Continuum to provide the ConPTY
backend. On macOS and Linux, Continuum uses the native PTY backend.
Interactive sessions use dedicated Claude Code, Codex and Gemini CLI adapter
profiles for startup, bounded context injection and visible status tracking.
Adapters report approval prompts but never approve them automatically.

Write an explicit handoff before switching agents:

```bash
continuum handoff \
  --task "Fix authentication retry behavior" \
  --next-step "Run the failing API test and fix the first assertion."
```

Resume with bounded context injected into another agent:

```bash
continuum resume gemini compact
continuum resume codex normal
```

Send a single message to an agent with bounded context:

```bash
continuum chat claude hi
continuum chat gemini normal inspect the card rules
continuum chat codex compact find the first failing test
```

Resume modes are `compact`, `normal` and `deep`. Prefer `compact` unless a
specific debugging task needs more retrieved context.

## Bridge An Existing Agent Session

If Claude Code, Codex or Gemini CLI was started manually in this project,
the running daemon detects it and publishes a compact context packet. Inspect
or bridge sessions explicitly:

```bash
continuum session detect
continuum session list
continuum session attach 12345
continuum session inject S0001 --mode compact
continuum session detach S0001
```

Use `continuum session detect --all` to see agents started from another
directory. Attaching one to this project requires the explicit
`--allow-other-project` option.

For a session Continuum did not launch, it can publish memory and MCP context,
track liveness and observe project file changes. It cannot retroactively read
that terminal's earlier output or silently type into that terminal.

## Inspect State And Memory

```bash
continuum status
continuum doctor
continuum search "authentication callback"
continuum memory retrieve "authentication callback" --semantic
continuum memory refresh ollama
continuum logs
```

Semantic memory commands require a configured and running Ollama embedding
model. Exact local search remains available without it.

## Configure Providers

List and test configured providers:

```bash
continuum providers list
continuum providers add ollama
continuum providers test ollama
continuum providers add openrouter
continuum providers test openrouter
```

If `model ask` fails, run the matching provider test first. Ollama errors
usually mean the local API is not running:

```bash
ollama serve
ollama pull llama3.1:8b
continuum providers test ollama
```

OpenRouter `401` errors mean the API key is missing or invalid:

```bash
export OPENROUTER_API_KEY="sk-or-..."
continuum providers test openrouter
```

Set up Ollama:

```bash
ollama pull llama3.1:8b
ollama pull nomic-embed-text
ollama serve
continuum providers test ollama
```

Set up OpenRouter:

```bash
export OPENROUTER_API_KEY=your_key_here
continuum providers add openrouter
continuum providers test openrouter
continuum model ask openrouter "Review the current handoff."
```

Ollama and OpenRouter are model providers. They cannot claim project files as
editing agents.

## Plan Or Run A Team

Create an editable starter team:

```bash
continuum team init default_dev_team
continuum team show default_dev_team
continuum route explain "Fix failing auth test"
```

Create controlled tasks without launching providers:

```bash
continuum team run default_dev_team "Fix failing auth test"
```

Run enabled providers sequentially with explicit writable file paths:

```bash
continuum team run default_dev_team "Fix failing auth test" \
  --execute \
  --allow-file src/auth.ts \
  --allow-file tests/auth.test.ts
```

## Delegate With Planner And Executor Models

Use `instruct` when a stronger planner should create a bounded execution
packet for a cheaper executor:

```bash
continuum instruct \
  --planner claude-opus-4-1-20250805 \
  --executor codex \
  --mode checkpoint \
  --scope src/auth.ts \
  --goal "Fix auth callback"
```

Continuum stores a graph in `.continuum/delegations/<id>/graph.json` and
generates a compact markdown packet for the executor. Use provider or agent
names for executors such as `codex`, `claude` or `gemini`. Use provider model
IDs for planner labels, for example `claude-opus-4-1-20250805`, `gemini-2.5-pro` or
`codex-mini-latest`.

Starter presets:

```text
default_dev_team
local_agent_team
local_only
review_heavy
fast_bugfix
research_then_code
```

## Manage Tasks And File Claims

```bash
continuum task create "Fix auth callback" --mode sequential
continuum task assign T0001 codex
continuum task claim T0001 codex src/auth/callback.ts src/auth/session.ts
continuum task complete T0001 --summary "Validation fixed; tests pass."
```

Only one active task can claim a file at a time. Completing a task releases
its claims.

## Use Git Worktrees

Schedule multiple isolated lanes for one objective:

```bash
continuum worktree schedule "Build auth flow with tests and docs" \
  --lane backend:claude:src/auth,src/api \
  --lane tests:codex:tests \
  --lane docs:gemini:docs \
  --depends-on tests:backend
```

Continuum creates one task, branch, worktree and compact context packet per
lane. File ownership is checked before scheduling; overlapping lanes are
rejected. Start a lane inside its isolated worktree with shared Continuum
memory:

```bash
continuum worktree resume T0001 claude compact
continuum worktree resume T0002 codex compact --interactive
```

Track gates and merge readiness:

```bash
continuum worktree schedules
continuum worktree schedule-status P0001
```

Parallel provider launching is still explicit: Continuum prepares safe lanes
and context, then you start each agent lane deliberately.

Create isolated work for a task:

```bash
continuum worktree create T0001
continuum worktree list
continuum worktree diff T0001
```

Before merging, record passing tests and approval for the exact worktree
commit:

```bash
continuum worktree test-result T0001 --pass --note "python -m unittest"
continuum worktree review T0001 --approve --note "reviewed"
continuum worktree merge T0001
```

Discard an isolated task branch:

```bash
continuum worktree discard T0001
```

## Plan Objectives And Evidence

Turn one goal into a coordinated plan:

```bash
continuum objective "Add Stripe billing" \
  --agent backend=claude \
  --agent tests=codex \
  --agent docs=gemini \
  --path backend=src,billing \
  --path tests=tests \
  --tests "python -m unittest" \
  --mode schedule
```

The planner creates tasks, context packets, next commands and, in schedule
mode, isolated worktree lanes where owned paths are known.

Inspect trust evidence for a task:

```bash
continuum evidence T0001
continuum evidence T0001 --json
continuum pr-packet T0001 --output .continuum/pr-packet-T0001.md
continuum flight-record T0001
continuum roi
```

Evidence includes claimed files, changed files, worktree state, test and review
gates, detected risks and the next safe action.

Inspect context quality:

```bash
continuum context enrich T0001
continuum context diff T0001 T0002
continuum context score T0001
```

Context intelligence reports relevant files, symbols, linked tests, recent
commits, decisions, blockers, token estimates, staleness, risk and missing
information.

Compare the same task with and without Continuum:

```bash
continuum benchmark capture T0001 --label with-continuum --output .continuum/bench-with.json
continuum benchmark compare --without bench-without.json --with .continuum/bench-with.json
```

Benchmark comparisons report tokens used, failed attempts, out-of-scope file
touches, tests run, context resets, human corrections, changed files and merge
readiness.

### End-To-End Trust Demo

The features above chain into one verifiable flow on any git project. Each step
reads recorded state rather than agent self-reports, so the final verdict is
evidence-backed:

```bash
# 1. Initialize and plan one objective into isolated worktree lanes.
continuum init
continuum objective "Add billing" \
  --agent backend=claude --agent tests=codex \
  --path backend=src/app.py --path tests=tests/test_app.py \
  --depends-on tests:backend \
  --mode schedule

# 2. Make the change inside the lane's worktree, then record the gates.
#    (Edit + commit happen in the worktree printed by `objective`/`worktree list`.)
continuum worktree test-result T0001 --pass --note "python -m unittest"
continuum worktree review T0001 --approve --note "scoped to src/app.py"

# 3. Inspect the evidence, build a reviewer packet and the flight record.
continuum evidence T0001          # changed files + PASS/APPROVED gates, no risks
continuum pr-packet T0001
continuum flight-record T0001     # final status: merge_ready

# 4. Quantify the run: ROI rollup and a with-vs-without benchmark comparison.
continuum roi                     # flight_records >= 1, merge_ready_tasks >= 1
continuum benchmark capture T0001 --label with-continuum --output bench-with.json
continuum benchmark compare --without bench-without.json --with bench-with.json
```

When the change stays inside each lane's claimed paths and both gates are
recorded against the current branch HEAD, `flight-record` reports
`merge_ready`, `roi` counts the merge-ready task, and `benchmark compare`
against a worse baseline reports "Continuum improved the measured run."

Note: lane scope is matched against changed files by exact path. Own the
precise files a lane edits (for example `--path backend=src/app.py`) so a
nested change is not flagged as an out-of-scope edit. `tests/test_demo_flow.py`
drives this whole flow end to end as an automated check.

## Governance Controls

Create and inspect a project policy:

```bash
continuum policy init
continuum policy show
```

Classify shell-command risk and scan content before it leaves the machine:

```bash
continuum command classify "rm -rf build"
continuum secrets scan .env
continuum audit export --format md --since 7d
```

Maintain MCP server and tool trust:

```bash
continuum mcp trust add local-tools --status untrusted
continuum mcp trust allow local-tools read_file
continuum mcp trust deny local-tools shell
continuum mcp trust list
```

## Connect MCP Agents

Start the project-scoped MCP stdio server:

```bash
continuum mcp serve --project /path/to/my-project
```

Equivalent MCP configuration:

```json
{
  "command": "continuum",
  "args": ["mcp", "serve", "--project", "/path/to/my-project"]
}
```

Available memory and task tools include:

```text
get_startup_context
get_current_state
get_latest_handoff
search_memory
expand_memory
get_raw_log
write_handoff
get_open_tasks
get_context_packet
get_workflows
post_agent_message
get_agent_messages
claim_task_files
complete_task
```

## Open Control Center

```bash
continuum ui --project . --open
```

The local UI displays current state and exposes explicit actions for team
editing, provider tests, workflow planning/execution and resume context.
Commands remain the primary interface.

## Start At Sign-In

```bash
continuum service install
continuum service status
continuum service remove
```

On Windows, the compatibility alias is also available:

```bash
continuum autostart install --vault "/path/to/your/Obsidian Vault/Agents"
```

## Optional Docker Mode

Run the Control Center UI in Docker against a mounted project:

```bash
PROJECT_DIR=/path/to/project docker compose --profile ui up
```

Start the optional Qdrant vector-service profile:

```bash
docker compose --profile vector up -d
```

## Stored Files

Project-local state:

```text
.continuum/
  events.jsonl        # event history
  events.sqlite3      # event and embedding database
  current.md          # latest compact state
  current_state.md    # expanded current-state view (events + active task)
  latest_handoff.md   # most recent handoff
  session_logs/       # agent session output (created on first run)
```

Optional Obsidian mirror (created when `--vault` is passed to `init`):

```text
Agents/
  Projects/
    my-project-a1b2c3d4/
      Current State.md
      Latest Handoff.md
      Sessions/
```

## Security

> **Read before using Continuum on sensitive projects.**

- `.continuum/` contains project memory and session logs — keep it out of Git.
- Do not publish session logs without reviewing them for secrets.
- Provider API keys are read from environment variables; never hardcode them.
- Team task steps require explicit file permissions before writing.
- Use `continuum handoff` before ending important interactive sessions.

## Documentation

**Getting Started**

- [Quickstart](docs/quickstart.md)
- [Getting Started](docs/getting-started.md)
- [Troubleshooting](docs/troubleshooting.md)

**Features**

- [Interactive CLI](docs/interactive-shell.md)
- [External Sessions](docs/external-sessions.md)
- [MCP Setup](docs/mcp-setup.md)
- [Providers](docs/providers.md)
- [Teams](docs/teams.md)
- [Tasks And Claims](docs/tasks-and-claims.md)
- [Task Worktrees](docs/worktrees.md)
- [Parallel Worktrees](docs/parallel-worktrees.md)
- [Workflow Timeline](docs/workflow-timeline.md)

**Strategy**

- [Product Strategy](docs/product-strategy.md)
- [Roadmap](docs/roadmap.md)

**Deployment**

- [Native Services](docs/services.md)
- [Optional Docker Mode](docs/docker.md)

**Release**

- [Release Notes](docs/releases/README.md)
- [Changelog](CHANGELOG.md)

## License

MIT
