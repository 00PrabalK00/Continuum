# Decisions

- Agent providers can operate on repositories; model providers are text/embedding backends and cannot edit files by default.
- Ollama is the local memory-intelligence backend; OpenRouter is the hosted optional reasoning/fallback backend.
- Team workflows are JSON configurations stored under `.continuum/teams/`.
- `team run` creates controlled tasks only in v0.1; it does not auto-launch providers.
- Only scoped compact context is loaded by default; semantic ranking over stored embeddings is future work.
- Continuum Control Center is a localhost developer console with real compact data views, not an analytics dashboard or autonomous agent runner.
- Control Center is read-only; commands own configuration and state changes.
- Initialization writes disabled provider starters and installs no team automatically.
- Alpha compaction is bounded delta notes plus targeted retrieval; semantic ranking is not part of the correctness path.
