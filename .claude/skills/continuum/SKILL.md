---
name: continuum
description: >-
  Shared project memory across AI agents. Use when resuming work, when asked what
  was happening, when running low on context, or to hand work to another AI.
---

## Continuum Shared Memory

This project uses Continuum, a local shared memory across AI coding agents.

At the start of a task, read the current context before asking the user to
re-explain anything:

- `.continuum/current.md` — where the work stands
- `.continuum/latest_handoff.md` — what the previous agent left for you

If the Continuum MCP server is connected, prefer its tools over reading files:
`get_startup_context` first, then `search_memory` and `expand_memory` for
targeted detail. Do not load full history by default.

## Recording progress

Record progress with the `save_progress` tool (or `continuum save "<what you
did> | <next step>"`), so the next session or a different agent continues
instead of starting over. Record:

- whenever the user asks you to save
- when you finish something worth not losing
- when you notice you are running low on context, before you run out, not after

You are the only one who can see how much context you have left, so this is
your call to make rather than something Continuum can detect for you.

If you already know the state and the next action, pass them. If you do not,
call `save_progress` with no arguments and Continuum writes the summary from
what it has recorded. Check `get_latest_handoff` first when you are unsure
whether something is already recorded, so you are not writing a handoff after
every message.

To hand work to a different AI, use `list_agents` and `ask_agent`.
