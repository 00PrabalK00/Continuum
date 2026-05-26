# v0.1 Context Continuity Demo

This demo proves the shipped feature: agents can continue work from compact
local memory and explicit handoffs. It does not demonstrate automatic team
execution.

## Prerequisites

```bash
continuum --version
claude --version
gemini --version
codex --version
```

Use `examples/multi-agent-handoff` as the demo repository.

## Recording Script

Terminal 1:

```bash
cd examples/multi-agent-handoff
continuum init
continuum up
continuum doctor
```

Terminal 2:

```bash
continuum task create "Validate greeting input with tests" --mode sequential
continuum task claim T0001 claude app.py test_app.py
continuum run claude
```

Ask Claude:

```text
Add input validation to greeting() and update tests. Stop after the first
working edit so another agent can continue.
```

Write the explicit checkpoint:

```bash
continuum handoff --task "Validate greeting input with tests" --next-step "Inspect Claude's changes and add missing edge-case coverage."
continuum resume gemini compact
```

Ask Gemini:

```text
Read the Continuum handoff and inspect the edited files. Identify remaining
edge cases and update the handoff for Codex.
```

Then:

```bash
continuum handoff --task "Validate greeting input with tests" --next-step "Run the tests, finish the patch and mark the task complete."
continuum resume codex compact
continuum task complete T0001 --summary "Greeting validation implemented and tests verified."
continuum ui --open
```

## Show In The Video

- `continuum status` before and after each handoff.
- `.continuum/latest_handoff.md` remaining compact.
- Control Center displaying the run and latest handoff.
- A conflicting claim rejection if a second task attempts to claim `app.py`.

The video should state directly: v0.1 preserves context; v0.2 will execute
planned workflows automatically.
