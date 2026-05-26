# Multi-Agent Handoff Demo

This small Python project is the v0.1 demo target. It starts with a simple
function and one passing test; ask agents to add validation and edge cases
while Continuum carries compact context between them.

```bash
continuum init
continuum up
continuum task create "Validate greeting input with tests" --mode sequential
continuum task claim T0001 claude app.py test_app.py
continuum run claude
continuum handoff --task "Validate greeting input with tests" --next-step "Inspect Claude's change and identify missing edge cases."
continuum resume gemini compact
continuum handoff --task "Validate greeting input with tests" --next-step "Run tests and finish the implementation."
continuum resume codex compact
continuum task complete T0001 --summary "Greeting validation complete and tests pass."
continuum ui --open
```

Continuum does not launch those agents automatically in v0.1. The explicit
switches are the point of this demo: memory survives while the worker changes.

Detailed recording instructions: [../../docs/demo.md](../../docs/demo.md).
