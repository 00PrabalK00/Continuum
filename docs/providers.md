# Providers

Continuum separates repo-operating agents from text/embedding model providers.

## Built-In Providers

| Provider | Type | Intended role |
| --- | --- | --- |
| `claude_code` | agent | implementation and refactors |
| `gemini_cli` | agent | exploration and broad review |
| `codex` | agent | patches and test repair |
| `ollama` | model | local summaries and embeddings |
| `openrouter` | model | hosted planning, review and fallback |

`continuum init` writes starter entries with all providers disabled. Enable only
the providers selected for a project:

```bash
continuum providers add codex
continuum providers add ollama
continuum providers add openrouter
continuum doctor
```

Configuration is stored in `.continuum/providers.json`.

## Ollama

Default local endpoint:

```text
http://localhost:11434/v1
```

Setup:

```bash
ollama pull llama3.1:8b
ollama pull nomic-embed-text
continuum providers add ollama
continuum providers test ollama
continuum model ask ollama "Compress this handoff."
continuum memory embed ollama
```

Embeddings are stored in the local SQLite memory database. Rank stored
embeddings for a targeted query with:

```bash
continuum memory retrieve "authentication callback" --semantic
```

## OpenRouter

Default endpoint:

```text
https://openrouter.ai/api/v1
```

Setup:

```bash
export OPENROUTER_API_KEY=your_key_here
continuum providers add openrouter
continuum providers test openrouter
continuum model ask openrouter "Review this implementation plan."
```

Continuum does not store the API key; it reads the configured environment
variable at request time.

## Agent Execution

Enabled CLI providers can be invoked sequentially through a team workflow:

```bash
continuum team run default_dev_team "Fix login crash" --execute --allow-file src/login.py
```

Continuum injects bounded context packets into those steps. It does not pass
permission-bypass flags to Claude, Gemini or Codex.
