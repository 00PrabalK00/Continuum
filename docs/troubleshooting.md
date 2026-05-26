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
| MCP not configured | Add a stdio server command: `continuum mcp serve --project "<path>"` |

Control Center exposes explicit workflow/team/provider actions. Use CLI
commands for low-level repairs, service management and audit-friendly scripts.
