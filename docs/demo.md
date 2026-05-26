# Context Continuity And Sequential Execution Demo

This demo first shows compact handoff continuation, then demonstrates explicit
sequential team execution. It does not demonstrate parallel writers.

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

Then demonstrate opt-in routing in a clean copy of the example project with
enabled providers:

```bash
continuum team init fast_bugfix
continuum team run fast_bugfix "Fix failing greeting test" --execute --allow-file app.py --allow-file test_app.py
```

The video should state directly: `v0.1` proved preserved context; `v0.2`
executes configured providers sequentially only when `--execute` is supplied.
