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
/handoff Fix authentication retry | Run the failing API test.
/resume codex compact
/memory authentication callback --semantic
/team run default_dev_team "Fix failing auth test"
/worktree list
/color off
/motion off
/quit
```

Agent targets have distinct terminal colors in the shell. Color and short
action animations can be disabled per session. Use `/terminal` or
`/resume-terminal` for live PTY/ConPTY sessions.

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
continuum run --interactive codex
continuum resume --interactive claude compact
```

On Windows, Python installs `pywinpty` with Continuum to provide the ConPTY
backend. On macOS and Linux, Continuum uses the native PTY backend.

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

Starter presets:

```text
default_dev_team
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

```powershell
continuum autostart install --vault "C:\Users\me\Documents\Obsidian Vault\Agents"
```

## Optional Docker Service

Continuum runs on the host. The Docker Compose file is only for its optional
vector-service profile:

```bash
docker compose --profile vector up -d
```

## Stored Files

Project-local state:

```text
.continuum/
  events.jsonl
  events.sqlite3
  current.md
  current_state.md
  latest_handoff.md
  session_logs/
```

Optional Obsidian mirror:

```text
Agents/
  Projects/
    my-project-a1b2c3d4/
      Current State.md
      Latest Handoff.md
      Sessions/
```

## Security Notes

- Keep `.continuum/` out of Git.
- Do not publish session logs without reviewing them for secrets.
- Provider keys are read from environment variables; do not put keys in notes.
- Writing team steps require explicit file permissions.
- Use `continuum handoff` before ending important interactive sessions.

## Documentation

- [Getting Started](docs/getting-started.md)
- [Interactive CLI](docs/interactive-shell.md)
- [External Sessions](docs/external-sessions.md)
- [Quickstart](docs/quickstart.md)
- [MCP Setup](docs/mcp-setup.md)
- [Providers](docs/providers.md)
- [Teams](docs/teams.md)
- [Tasks And Claims](docs/tasks-and-claims.md)
- [Task Worktrees](docs/worktrees.md)
- [Native Services](docs/services.md)
- [Optional Docker Mode](docs/docker.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Release Notes](docs/releases/README.md)
- [Changelog](CHANGELOG.md)

## License

MIT
