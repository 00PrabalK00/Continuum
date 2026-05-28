# Optional Docker Mode

Docker is optional. The native host install remains the default because
Continuum's daemon, file watcher and agent CLI wrappers need direct access to
your local repository and installed agent CLIs.

## Control Center UI

Run the read-only Control Center UI against a mounted project:

```bash
PROJECT_DIR=/path/to/project docker compose --profile ui up
```

The UI binds to `127.0.0.1:7357`. The container receives the project at
`/project` and starts:

```bash
continuum ui --host 0.0.0.0 --port 7357
```

## Vector Service

The supplied Compose profile can also start an optional local Qdrant vector
service:

```bash
docker compose --profile vector up -d
docker compose --profile vector down
```

It exposes Qdrant only on `127.0.0.1:6333` and stores vector data in the
Docker volume `continuum_qdrant`. This profile does not upload project memory.

Ollama-backed SQLite embeddings remain the supported default memory path in
this alpha. Qdrant is available for subsequent large-index adapters.
