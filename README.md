<p align="center">
  <img src="continuum/ui/logo.svg" alt="Continuum logo" width="152">
</p>

<h1 align="center">Continuum</h1>

<p align="center"><strong>Continuum is a local coordination layer for AI coding teams.</strong></p>

It gives Claude Code, Codex, Gemini CLI, Ollama and OpenRouter shared memory,
task ownership, file-claim safety and repeatable planned team workflows.

```text
Claude Code       Codex CLI       Gemini CLI
      \               |               /
       \              |              /
        +-------- Continuum --------+
                    |
          .continuum local memory
                    |
         optional Obsidian notes
```

Continuum does not replace an agent. It is the shared memory and task-planning
layer: it preserves what changed, assigns scoped work, rejects conflicting
file claims and carries compact context to the next worker.

## Release Promise

```text
v0.1 proves Continuum can preserve context across agents.
v0.2 proves Continuum can automatically orchestrate agents.
```

Version `0.1` ships shared memory, compact handoffs, provider configuration,
team planning, MCP memory tools, controlled tasks and a read-only Control
Center. It does not claim autonomous multi-agent execution.

## Status

This repository is a `v0.1.1` alpha baseline focused on context continuity
between agent sessions.

Shipped in `v0.1`:

- `continuum init` creates compact shared-memory guidance for supported agents.
- `continuum daemon` watches local file changes and refreshes the handoff.
- `continuum run` records an agent session and creates bounded notes.
- `continuum resume` starts another agent with compact, normal or deep bounded context.
- `continuum handoff` records the current task and exact next action.
- `continuum status` reports daemon, storage, provider, task, claim and mirror state.
- `continuum doctor` diagnoses installation and configured integrations with specific fixes.
- `continuum search` searches recorded local event memory.
- `continuum mcp serve` exposes progressive, budgeted memory tools to MCP-compatible agents.
- `continuum task` creates routed tasks and exclusive file claims for controlled workers.
- Ollama and OpenRouter model backends for text-only memory/reasoning calls.
- `Continuum Teams` JSON presets for configurable role-based workflow planning.
- **Continuum Control Center**, a read-only local developer-console UI over real project state.
- Optional Obsidian mirroring with one small folder per project.
- Windows `continuum autostart install` for sign-in startup.

Deferred to `v0.2`, not claimed as shipped:

- Semantic/vector retrieval over older memories.
- Automated execution of Team workflows across provider adapters.
- Fully interactive PTY-aware wrappers across operating systems.
- macOS/Linux background service installers.
- Optional Docker mode.

## Installation Strategy

The product direction is:

```text
npm front door -> host-local daemon -> MCP tools -> optional Obsidian mirror
                                      -> optional Docker services later
```

Docker is not required for normal use because the daemon must see local files
and run local agent CLIs. The initial npm package bundles the Python core and
requires Python 3.9 or newer on the host. A native packaged daemon is a later
distribution improvement.

## Try From Source

Requires Python 3.9 or newer.

```bash
git clone <your-continuum-repository-url>
cd continuum
python -m pip install -e .
```

Or exercise the npm front door locally:

```bash
npm link
continuum --version
```

The intended npm install experience is:

```bash
npx -y continuum-agent-memory@latest init
npx -y continuum-agent-memory@latest up
npx -y continuum-agent-memory@latest ui --open
```

Until the npm publication issue is complete, run from GitHub without a global install:

```bash
npx -y github:00PrabalK00/Continuum --version
```

GitHub Actions now runs install smoke coverage for Windows, macOS and Linux.
The public milestone is tracked in [issue #8](https://github.com/00PrabalK00/Continuum/issues/8):
fresh-machine setup in under two minutes.

## Context Continuity Demo

```bash
cd examples/multi-agent-handoff
continuum init
continuum up
continuum run claude
continuum handoff --task "Add greeting validation" --next-step "Inspect Claude's edit and continue implementation."
continuum resume gemini compact
continuum handoff --task "Add greeting validation" --next-step "Run tests and finish the patch."
continuum resume codex compact
continuum ui --open
```

This is the `v0.1` proof: Claude starts work, Gemini and Codex receive compact
handoffs instead of a full transcript, and Control Center shows the local
memory and run state. See [docs/demo.md](docs/demo.md) for a recording script.

Team planning is deterministic but does not execute agents:

```bash
continuum providers add codex
continuum route explain "Fix failing auth test"
continuum team run default_dev_team "Fix failing auth test"
```

`team run` creates planned controlled tasks only. It does not launch agents.

## Quickstart From Source

Inside a project:

```bash
continuum init --vault "/path/to/your/Obsidian Vault/Agents"
continuum team init default_dev_team
continuum doctor
continuum up
```

In another terminal, run an installed agent:

```bash
continuum run codex
continuum run claude
continuum run gemini
```

Configure model backends and inspect routing:

```bash
continuum providers add ollama
continuum providers test ollama
continuum providers add openrouter
continuum providers test openrouter
continuum model ask ollama "Summarize the latest handoff in three bullets."
continuum route explain "fix failing auth test"
continuum ui --open
```

Before switching agents, record an explicit handoff:

```bash
continuum handoff \
  --task "Implement authentication retry behavior" \
  --next-step "Run the failing API test and fix the first assertion."

continuum resume gemini
```

On Windows, start the daemon automatically after sign-in:

```powershell
continuum autostart install --vault "C:\Users\me\Documents\Obsidian Vault\Agents"
```

## What It Writes

Each project stores raw local runtime data in:

```text
.continuum/
  events.jsonl
  events.sqlite3
  current.md
  current_state.md
  latest_handoff.md
  session_logs/
```

When an Obsidian folder is configured, only compact notes are mirrored:

```text
Agents/
  Memory Index.md
  Projects/
    my-project-a1b2c3d4/
      Current State.md
      Latest Handoff.md
      Sessions/
```

Agents start from `current.md`, not full logs. They retrieve current state,
handoffs, memories or raw logs only when required.
This prevents project history from consuming context unnecessarily.

## Commands

| Command | Purpose |
| --- | --- |
| `continuum init` | Initialize memory and agent instruction files |
| `continuum up` | Start the project daemon in the background |
| `continuum down` | Stop the project daemon |
| `continuum logs` | Read background daemon output |
| `continuum daemon` | Run the watcher in the foreground |
| `continuum run codex` | Run an agent through session recording |
| `continuum resume gemini compact` | Continue with the smallest context budget |
| `continuum handoff` | Write current task and next action explicitly |
| `continuum status` | Report daemon, storage, MCP, provider, task, claim and mirror state |
| `continuum doctor` | Run deterministic checks with specific remediation actions |
| `continuum search "retry"` | Search local event history |
| `continuum mcp serve` | Serve project memory tools over MCP stdio |
| `continuum task create "Fix auth"` | Create a controlled task |
| `continuum task claim T0001 codex src/auth.ts` | Exclusively claim a file |
| `continuum task complete T0001 --summary "Fixed"` | Complete work and release claims |
| `continuum providers list` | List CLI agents and model backends |
| `continuum providers test ollama` | Check a local Ollama endpoint |
| `continuum model ask openrouter "Review plan"` | Make a text-only model call |
| `continuum memory embed ollama` | Embed compact current context locally |
| `continuum team init default_dev_team` | Create an editable JSON starter team |
| `continuum team run default_dev_team "Fix auth"` | Create a routed task plan |
| `continuum route explain "Fix auth"` | Explain selected team workflow |
| `continuum ui --open` | Open the local Control Center web console |
| `continuum autostart install` | Start the daemon at Windows sign-in |

## MCP

Continuum exposes project-scoped progressive retrieval tools:

```text
get_startup_context
get_current_state
get_latest_handoff
search_memory
expand_memory
get_raw_log
write_handoff
get_open_tasks
claim_task_files
complete_task
```

Configure an MCP-compatible agent with a stdio server command equivalent to:

```json
{
  "command": "continuum",
  "args": ["mcp", "serve", "--project", "/path/to/my-project"]
}
```

Each server is scoped to one project, so an agent does not receive unrelated
project history by default.

## Context Budgets

Automatic context is deliberately small:

| Layer | Default budget |
| --- | ---: |
| Startup `current.md` | 800 tokens |
| Latest handoff | 1,200 tokens |
| Default retrieved memory response | 2,000 tokens |
| Raw log response | 1,000 tokens |
| Deep resume cap | 6,000 tokens |

The SQLite event history can grow; automatic prompt context does not grow with
it. The compaction algorithm is intentionally simple and stable:

1. Store full event history locally, outside automatic prompts.
2. Write delta-based `current.md` and `latest_handoff.md` within fixed budgets.
3. Start resumes from compact state and report the estimated token count.
4. Retrieve event matches only for a targeted query.
5. Expand raw logs or individual memory events only on demand.

This bounds repeated input cost without relying on an unverified summarization
model. Semantic ranking can be added after exact retrieval remains stable.

## Compact Speech

The generated agent guidance asks agents to keep routine updates factual and
brief: completed action, next action, blocker. This reduces output overhead;
the context-budget and retrieval model is what prevents growing input cost.

## Controlled Tasks

Continuum models workers as scoped executors rather than agents talking to
each other freely:

```bash
continuum task create "Fix auth callback" --mode sequential
continuum task assign T0001 codex
continuum task claim T0001 codex src/auth/callback.ts src/auth/session.ts
continuum task complete T0001 --summary "Validation fixed; tests pass."
```

Task status is structured (`CREATED`, `ASSIGNED`, `RUNNING`, `REVIEWING`,
`TESTING`, `DONE`, `BLOCKED`, `FAILED`, `NEEDS_USER`). A claimed file cannot
be claimed by a different active task; finalizing a task releases its claims.

Automatic provider selection, sequential Teams execution, PTY-backed wrappers,
semantic retrieval and isolated Git worktree merging are not shipped in
`v0.1`.

## Providers

Continuum has two provider classes:

| Type | Providers | Permission boundary |
| --- | --- | --- |
| Agent workers | Claude Code, Gemini CLI, Codex | May edit/run commands through their adapters |
| Model backends | Ollama, OpenRouter | Text/embedding work only by default |

`Ollama` uses its local OpenAI-compatible endpoints for chat and embeddings.
It is intended for private summaries, compression and embeddings:

```bash
ollama pull llama3.1:8b
ollama pull nomic-embed-text
continuum providers add ollama
continuum providers test ollama
continuum memory embed ollama
```

`OpenRouter` uses its OpenAI-compatible hosted API for optional planning,
review and fallback:

```bash
export OPENROUTER_API_KEY=your_key_here
continuum providers add openrouter
continuum providers test openrouter
continuum model ask openrouter "Review the current handoff."
```

Provider configuration lives in `.continuum/providers.json`. `continuum init`
writes disabled starter entries only; enable each provider explicitly with
`continuum providers add <provider>`.

## Continuum Teams

**Configure your own AI engineering team.**

```bash
continuum team init default_dev_team
continuum team show default_dev_team
continuum team run default_dev_team "Fix login crash with tests"
```

Presets are editable starter configurations, created only when requested:

```text
default_dev_team
local_only
review_heavy
fast_bugfix
research_then_code
```

The default development starter routes a bug fix as:

```text
Gemini exploration -> OpenRouter reasoning -> Claude coding -> Codex tests -> Ollama memory
```

Team definitions are JSON files under `.continuum/teams/`. Continuum does not
select a team during `init`. `team run` validates permissions and creates
planned controlled tasks; automatic provider launching is not enabled in this
version. Model roles cannot edit files, and write-capable tasks remain subject
to exclusive file claims.

## Continuum Control Center

Run a local UI against the current project:

```bash
continuum ui --project . --open
```

The Control Center is served only on `127.0.0.1` by default and is read-only.
Commands remain the main interface for configuration and mutations. It shows:

- Overview with daemon state, project, team, providers and latest handoff.
- Projects with Git/memory/Obsidian paths.
- Teams with configured roles and terminal command references.
- Providers with configured status and terminal health-check references.
- Memory with compact startup state and exact event search.
- Runs and Handoffs backed by structured tasks and bounded Markdown.
- Settings showing local storage and context budgets.

It cannot create tasks, start daemons, test providers or write handoffs.
It is intentionally not a transcript dump or decorative analytics dashboard.
Raw logs remain behind deliberate retrieval paths.

## Safety And Privacy

Continuum is local-first. Files, SQLite events and optional Obsidian notes stay
on your computer. Session logging may contain prompts, responses, command
output or secrets printed by an agent. Keep `.continuum/` out of Git and
review notes before publishing them.

Model providers (`ollama`, `openrouter`) cannot be assigned as file editors by
team validation. Provider keys are read from environment variables and are not
written to events, notes or Obsidian.

## Current Wrapper Boundary

`continuum run` captures subprocess output and estimates checkpoint usage from
captured text. Provider CLIs do not expose uniform exact context usage, so the
80 percent threshold is an estimate. Some full-screen interactive agent modes
also require future PTY-specific adapters; use explicit `continuum handoff`
checkpoints for important work.

## Documentation

- [Architecture](docs/architecture.md)
- [Quickstart](docs/quickstart.md)
- [Getting Started](docs/getting-started.md)
- [v0.1 Demo Script](docs/demo.md)
- [Tasks and Claims](docs/tasks-and-claims.md)
- [Memory Model](docs/memory-model.md)
- [Troubleshooting](docs/troubleshooting.md)
- [MCP Integration](docs/mcp.md)
- [MCP Setup](docs/mcp-setup.md)
- [Providers](docs/providers.md)
- [Teams](docs/teams.md)
- [Control Center](docs/control-center.md)
- [Roadmap](docs/roadmap.md)
- [Contributing](CONTRIBUTING.md)

## License

MIT
