# Memory Model

Continuum minimizes repeated prompt input by separating durable memory from
automatically supplied context.

1. SQLite and JSONL retain local events.
2. `current.md` and `latest_handoff.md` hold bounded delta summaries.
3. `continuum resume <agent> compact` prints its estimated context size before
   starting the agent.
4. MCP search retrieves matching compressed events only when requested.
5. Raw logs are read only for a specific debugging need.

This is deterministic compaction: historical storage can grow, but startup
context remains bounded. Semantic ranking is intentionally not required for
the alpha correctness path.
