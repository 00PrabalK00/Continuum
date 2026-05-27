# External Agent Sessions

Continuum can bridge supported agent CLIs that were launched manually:

```bash
continuum session detect
continuum session attach <pid>
continuum session list
continuum session inject S0001 --mode compact
continuum session detach S0001
```

When `continuum up` is running, Claude Code, Codex or Gemini CLI processes
whose working directory is the watched project are attached automatically.
Continuum creates a bounded packet at:

```text
.continuum/external_sessions/S0001/context.md
```

An MCP-enabled external agent can retrieve that packet with
`get_external_session_context`. The packet contains current state and a
continuation instruction, not historical transcript dumps.

## Project Safety

Processes started outside the current project appear only with:

```bash
continuum session detect --all
```

Bind one intentionally with:

```bash
continuum session attach <pid> --allow-other-project
```

## Terminal Boundary

Terminal ownership is established when a process is launched. For a CLI that
was not started by Continuum, the bridge records identity and liveness,
publishes bounded context, exposes MCP memory and lets the existing daemon
observe file changes. It does not intercept earlier terminal output or inject
keystrokes into an arbitrary terminal window.
