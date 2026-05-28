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
/
/help
/status [--events]
/doctor
/up
/down
/logs
/handoff <current task> | <next exact step>
/run [claude|codex|gemini] [agent arguments]
/terminal [claude|codex|gemini] [agent arguments]
/resume [claude|codex|gemini] [compact|normal|deep] [agent arguments]
/switch <claude|codex|gemini> [compact|normal|deep] [agent arguments]
/chat [claude|codex|gemini] [compact|normal|deep] <message>
/resume-terminal [claude|codex|gemini] [compact|normal|deep] [agent arguments]
/search <query>
/memory <query> [--semantic]
/providers <list|add|test> ...
/model <ask> ...
/team <init|list|show|explain|run> ...
/plan <request>
/task <create|list|show|assign|claim|status|complete> ...
/session <detect|attach|list|refresh|inject|detach> ...
/worktree <create|list|diff|test-result|review|merge|discard> ...
/route <request>
/service <install|status|remove>
/ui
/mcp [serve]
/clear
/quit
```

Typing `/` by itself shows the available slash commands.

Typing plain text sends that message to the currently selected agent with
compact Continuum context:

```text
continuum[claude]> hi
```

This is equivalent to:

```text
/chat claude compact hi
```

Any current `continuum` command can be used as a slash command. The shell
injects the active `--project` and optional `--vault` into known command
positions, so `/context build coder --mode compact` maps to the same scoped
CLI action as `continuum context build --project <project> coder --mode
compact`. Exact CLI-style forms such as `/run --interactive codex` and
`/route explain "fix auth"` are also supported. If you pass `--project`
yourself, the shell preserves it.

Bracketed paste input is ingested with paste elision. The shell stores the
full pasted content internally, shows a compact `{n chars}` receipt and then
dispatches the command with the full text preserved.

`/switch gemini normal` changes the selected agent and dispatches
`continuum resume --project <project> gemini normal`, so the next CLI receives
the latest bounded Continuum context automatically.

Plain `/mcp` prints the dedicated MCP server command because an MCP stdio
server should not take over the interactive console process. `/mcp serve`
dispatches the actual project-scoped server command.

## Live Terminal Sessions

`/run` and `/resume` retain captured-output behavior. Use `/terminal` and
`/resume-terminal` to launch an agent through a real terminal:

```text
/terminal claude
/resume-terminal codex compact
```

The terminal backend is native PTY on macOS/Linux and `pywinpty` on Windows.
Continuum records the streamed terminal transcript and preserves checkpoint
handoff behavior. It does not attach to externally launched CLI sessions or
automatically schedule parallel agent worktrees.
