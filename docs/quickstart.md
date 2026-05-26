# Quickstart

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

## Record Work

```bash
continuum handoff --task "Fix login timeout" --next-step "Run the failing login test."
continuum run codex
continuum resume claude compact
```

## Inspect Memory

```bash
continuum status
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
