# Decisions

- Agent providers can operate on repositories; model providers are text/embedding backends and cannot edit files by default.
- Ollama is the local memory-intelligence backend; OpenRouter is the hosted optional reasoning/fallback backend.
- Team workflows are JSON configurations stored under `.continuum/teams/`.
- `team run` remains planning-only by default; `--execute` opts into sequential provider execution in v0.2.
- Only scoped compact context is loaded by default; Ollama semantic ranking is explicitly requested and bounded.
- Continuum Control Center is a localhost developer console with real compact data views, not an analytics dashboard or autonomous agent runner.
- Control Center is read-only; commands own configuration and state changes.
- Initialization writes disabled provider starters and installs no team automatically.
- Alpha compaction is bounded delta notes plus targeted retrieval; semantic ranking is not part of the correctness path.
- Branding uses one packaged SVG asset shared by GitHub README and Control Center to avoid redundant large raster assets.
- v0.1 promises cross-agent context continuity only; automatic execution belongs to v0.2.
- npm is the intended user front door; publishing remains a tracked release issue until completed.
- Automatic writer execution requires explicit file claims and stops when unrelated dirty paths prevent enforcement.
- `continuum resume` injects bounded startup context only into CLIs launched through Continuum; external sessions are not auto-detected.
