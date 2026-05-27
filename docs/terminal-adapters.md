# Interactive Terminal Adapters

Continuum launches interactive Claude Code, Codex and Gemini CLI sessions
through provider-specific adapters layered over its PTY/ConPTY terminal
transport.

```bash
continuum adapters list
continuum run --interactive claude
continuum resume --interactive gemini compact
continuum run --interactive codex -- --no-alt-screen
```

## Behavior

| Agent | Initial context behavior | Session behavior |
| --- | --- | --- |
| Claude Code | Appends the bounded handoff as the initial positional prompt. | Observes explicit approval and error text from the terminal. |
| Codex | Opens the interactive TUI scoped with `-C <project>` unless a scope is already supplied, then appends initial context. | Observes explicit approval and error text from the terminal. |
| Gemini CLI | Injects bounded context through `--prompt-interactive`, merging an existing prompt option when present. | Observes explicit approval and error text from the terminal. |

Each interactive session stores its adapter, terminal phase and explicit
approval/input prompt counts in `.continuum/token_usage.json`. State
transitions are written to the local event store as
`terminal_adapter_status` events.

## Safety Boundary

The adapters parse visible terminal text conservatively. They do not parse
hidden reasoning, automatically respond to approval prompts or add permission
bypass modes. Existing terminals that Continuum did not launch remain
cooperative bridges: Continuum can publish context for them, but cannot
retroactively capture their prior terminal output or type into them silently.
