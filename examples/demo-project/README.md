# Demo Project

From this folder:

```bash
continuum init --vault "/path/to/Obsidian Vault/Agents"
continuum handoff --task "Add a health endpoint" --next-step "Inspect the app entrypoint."
continuum daemon
```

Then open another terminal and run one of the installed coding agents through
`continuum run <agent>`.
