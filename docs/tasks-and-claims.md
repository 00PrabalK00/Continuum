# Tasks And Claims

Continuum uses tasks and exclusive file claims to make agent ownership visible
before edits occur.

```bash
continuum task create "Fix auth callback" --mode sequential
continuum task assign T0001 codex
continuum task claim T0001 codex src/auth/callback.ts
continuum task complete T0001 --summary "Validation fixed; tests pass."
```

A claimed path cannot be claimed by a different active task. Completing a task
releases its claims. Model providers such as Ollama and OpenRouter cannot be
selected as task file-editing agents.

Use `continuum status` to inspect open tasks, running tasks and claim counts.
