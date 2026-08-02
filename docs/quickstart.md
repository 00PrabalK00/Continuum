# Quickstart

## The Daily Loop

One command covers everyday use. It auto-initializes the project, injects the
previous context, records the session and writes the next handoff on exit.

```bash
# Work through Continuum; the handoff is written when the agent exits:
continuum go

# Out of context? Run it again — the work moves to the next available agent:
continuum go

# Name any agent CLI on your PATH explicitly:
continuum go codex
continuum go hermes

# Or paste the same context into any AI chat (web ChatGPT, claude.ai, ...):
continuum copy
```

Bare `continuum` prints where you left off. Optional one-time
`continuum setup` connects installed agent CLIs over MCP.
`continuum save "<task> | <next step>"` still records a handoff by hand when
you want one mid-session.

`continuum --help` lists only these commands. Everything else — teams,
worktrees, governance, evidence — is listed by `continuum help --all` and
still runs exactly as before.

## Target npm Flow

After npm publishing is complete, the main user path is:

```bash
npx -y continuum-agent-memory@latest init
npx -y continuum-agent-memory@latest up
npx -y continuum-agent-memory@latest ui --open
```

For the GitHub alpha source checkout, use the commands below.

## Initialize A Project

```bash
cd /path/to/my-project
continuum init
continuum team init default_dev_team
continuum doctor
```

With an Obsidian mirror:

```bash
continuum init --vault "/path/to/Obsidian Vault/Agents"
```

The initialization step preserves existing agent instruction files and appends
only the Continuum memory block if it is missing.

Initialization writes disabled provider starter entries. Enable only the
backends chosen for the project:

```bash
continuum providers add codex
continuum providers add ollama
```

## Watch Changes

```bash
continuum up
```

For Windows sign-in startup:

```powershell
continuum autostart install
```

On macOS or Linux, use native service definitions:

```bash
continuum service install
continuum service status
```

For isolated writer work:

```bash
continuum worktree create T0001
continuum worktree test-result T0001 --pass --note "tests passed"
continuum worktree review T0001 --approve --note "reviewed"
continuum worktree merge T0001
```

## Record Work

```bash
continuum handoff --task "Fix login timeout" --next-step "Run the failing login test."
continuum run codex
continuum resume claude compact
```

## Inspect Memory

```bash
continuum status
continuum doctor
continuum search "login timeout"
```

Open `.continuum/current.md` for the smallest continuation context.

## Connect MCP

Point an MCP-compatible client at:

```json
{
  "command": "continuum",
  "args": ["mcp", "serve", "--project", "/path/to/my-project"]
}
```
