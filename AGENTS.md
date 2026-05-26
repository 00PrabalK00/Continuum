# Continuum Development Instructions

Keep Continuum local-first and project-scoped.

- Compact handoffs are the default agent context; raw logs must not become the
  default read path.
- Implemented claims in the README must correspond to tested functionality.
- MCP tools must remain scoped to the initialized project passed to the server.
- Docker integrations are optional additions, not required for host-side file
  watching or local agent execution.

Run before completing changes:

```bash
python -m unittest discover -s tests -v
python -m continuum --help
node bin/continuum.js --version
```

## Continuum Shared Memory

Before continuing prior work, read:

- `.continuum/current.md`

Use Continuum MCP for targeted retrieval: read current state or latest handoff
only when needed, search exact topics, then expand specific memory IDs. Do not
load full historical logs by default.

Use the existing handoff and decisions. Before stopping substantive work,
write a Continuum handoff with the exact next action.

Keep status output compact: facts, actions and blockers; no filler.
