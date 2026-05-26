# Optional Docker Mode

Docker is optional. Continuum's daemon, file watcher and agent CLI wrappers
must continue running on the host so they can operate on your local repository.

The supplied Compose profile starts an optional local Qdrant vector service:

```bash
docker compose --profile vector up -d
docker compose --profile vector down
```

It exposes Qdrant only on `127.0.0.1:6333` and stores vector data in the
Docker volume `continuum_qdrant`. This profile does not upload project memory
or start AI agents.

Ollama-backed SQLite embeddings remain the supported default memory path in
this alpha. Qdrant is available for subsequent large-index adapters.
