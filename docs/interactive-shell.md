# Interactive CLI

Start a project-scoped command console:

```bash
continuum shell
continuum shell --agent codex --color auto --animation auto
```

The shell is a control surface over existing Continuum commands. It preserves
the current project path and optional Obsidian vault path for each command.

## Terminal Legend

Use `/terminals` to inspect agent targets and model backends. The shell uses
distinct ANSI colors for:

| Target | Role |
| --- | --- |
| Claude | Agent session target |
| Codex | Agent session target |
| Gemini | Agent session target |
| Ollama | Local text/embedding provider |
| OpenRouter | Hosted text provider |

Change defaults for the current console:

```text
/agent gemini
/color on
/motion off
```

## Slash Commands

```text
/help
/status [--events]
/doctor
/up
/down
/logs
/handoff <current task> | <next exact step>
/run [claude|codex|gemini] [agent arguments]
/resume [claude|codex|gemini] [compact|normal|deep] [agent arguments]
/search <query>
/memory <query> [--semantic]
/providers <list|add|test> ...
/model <ask> ...
/team <init|list|show|explain|run> ...
/plan <request>
/task <create|list|show|assign|claim|status|complete> ...
/worktree <create|list|diff|test-result|review|merge|discard> ...
/route <request>
/service <install|status|remove>
/ui
/mcp
/clear
/quit
```

`/mcp` prints the dedicated MCP server command because an MCP stdio server
should not take over the interactive console process.

## Current Terminal Boundary

`/run` and `/resume` invoke Continuum's current captured-output session
wrapper. The interactive shell itself does not provide PTY/ConPTY-backed
terminal emulation, attach to externally launched CLI sessions or
automatically schedule parallel agent worktrees. Those require agent-specific
and operating-system-specific terminal adapters.
