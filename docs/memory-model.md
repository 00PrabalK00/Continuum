# Memory Model

Continuum minimizes repeated prompt input by separating durable memory from
automatically supplied context.

1. SQLite and JSONL retain local events.
2. `current.md` and `latest_handoff.md` hold bounded delta summaries.
3. `continuum resume <agent> compact` reports its estimated size and injects
   that bounded context as the launched agent's initial prompt.
4. Orchestrated steps receive bounded role packets plus relevant result messages.
5. MCP/CLI retrieval returns exact matches first; Ollama semantic ranking is opt-in.
6. Raw logs are read only for a specific debugging need.

This is deterministic compaction: historical storage can grow, but startup
context remains bounded. Semantic ranking is optional and never dumps raw logs
into startup context.
