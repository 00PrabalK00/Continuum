# Claude Instructions

## Continuum Shared Memory

This project uses Continuum, a local shared memory across AI coding agents.

At the start of a task, read the current context before asking the user to
re-explain anything:

- `.continuum/current.md` — where the work stands
- `.continuum/latest_handoff.md` — what the previous agent left for you

## The tools

If the Continuum MCP server is connected, prefer its tools over reading the
files. Start narrow and expand; do not load full history by default.

| What you need | MCP tool | Without MCP |
| --- | --- | --- |
| Where the work stands | `get_startup_context` | `continuum status` |
| What the last agent left | `get_latest_handoff` | read `.continuum/latest_handoff.md` |
| One exact topic | `search_memory` | `continuum search "<topic>"` |
| Full text behind a result | `expand_memory` | `continuum log` |
| Record what you did | `save_progress` | `continuum save "<did> \| <next step>"` |
| Hand the work over | `write_handoff` | `continuum handoff --task "<state>" --next-step "<next>"` |
| Reach another agent | `list_agents`, `ask_agent` | `continuum ask <agent> "<question>"` |

`get_raw_log` returns the unsummarised history. It is a last resort, not the
read path — the compact views above are what keep this cheap.

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
