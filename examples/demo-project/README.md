# Demo Project

This minimal setup demonstrates initializing local memory before running any
agent:

```bash
continuum init --vault "/path/to/Obsidian Vault/Agents"
continuum doctor
continuum up
continuum handoff --task "Add a health endpoint" --next-step "Inspect the app entrypoint."
```

For the complete Claude to Gemini to Codex handoff story, use
[`../multi-agent-handoff`](../multi-agent-handoff/README.md).
