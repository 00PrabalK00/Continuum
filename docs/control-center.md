# Continuum Control Center

Control Center is a local web interface for inspecting a Continuum project and
running explicit command-parity actions. The CLI remains the canonical
automation and scripting surface.

```bash
continuum ui --project /path/to/project --open
```

By default it listens on `127.0.0.1:7357`. It does not expose an internet
service or upload project data.

Supported actions:

- Create or save a validated team configuration.
- Test a selected configured provider.
- Plan a workflow or explicitly invoke sequential execution.
- Build compact resume context for a selected role.

## Pages

| Page | Backed by |
| --- | --- |
| Overview | Daemon PID, configured project, providers, latest compact handoff and tasks |
| Projects | `.continuum/config.json`, Git branch and optional Obsidian mirror |
| Teams | `.continuum/teams/*.json` and the CLI commands that plan workflows |
| Providers | `.continuum/providers.json` and CLI health-check references |
| Memory | `current.md`, bounded handoff files and SQLite event search |
| Runs | Structured task records, file claims and workflow timeline state |
| Handoffs | Bounded handoff reader and CLI command reference |
| Settings | Local storage and context budget boundary |

## Workflow Timeline

The Control Center should include a workflow timeline for real Continuum
scheduling state. Each lane represents an agent, model provider, tool or user
approval step. Task blocks show status, start/end time, dependencies, claimed
or changed files, context packets, handoff points and blockers.

The timeline is a visualization of recorded tasks, workflow messages, file
claims, context packets, handoffs and worktree gates. It must label planned
work as planned and running work as running only when Continuum has recorded
that state.

## Design Boundary

The UI exposes GET-only project views. It does not start or stop the daemon,
write a handoff, test providers or plan tasks. Run `continuum` commands for
those operations. It does not invent provider costs, productivity scores or
autonomous execution state.
