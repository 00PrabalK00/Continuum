# Troubleshooting

Start with:

```bash
continuum status
continuum doctor
```

Common actions:

| Failure | Next action |
| --- | --- |
| Daemon stopped | `continuum up` |
| Missing team | `continuum team init default_dev_team` |
| Ollama disabled | `continuum providers add ollama` |
| Ollama API unavailable | `ollama serve` then `continuum providers test ollama` |
| OpenRouter key missing on PowerShell | `$env:OPENROUTER_API_KEY="<key>"; continuum providers test openrouter` |
| OpenRouter HTTP 401 | Replace the key with `$env:OPENROUTER_API_KEY="sk-or-..."`, then run `continuum providers test openrouter` |
| MCP not configured | Add a stdio server command: `continuum mcp serve --project "<path>"` |

Control Center exposes explicit workflow/team/provider actions. Use CLI
commands for low-level repairs, service management and audit-friendly scripts.

## Model Providers Do Not Respond

Check the installed version and provider directly:

```bash
continuum --version
continuum providers test ollama
continuum providers test openrouter
```

For Ollama, start the local API and pull the default model:

```bash
ollama serve
ollama pull llama3.1:8b
continuum providers test ollama
```

For OpenRouter on PowerShell:

```powershell
$env:OPENROUTER_API_KEY="sk-or-..."
continuum providers test openrouter
```

## Large Pasted Prompts

`model ask` accepts unquoted multi-word prompts:

```bash
continuum model ask ollama summarize this pasted text
continuum model ask openrouter "summarize this pasted text"
```

Inside `continuum shell`, bracketed paste input is shown as `{n chars}` while
the full pasted content is still dispatched to the command.
