# Continuum Teams

Continuum Teams lets a project configure role-based AI workflows in JSON.

```bash
continuum team init default_dev_team
continuum team show default_dev_team
continuum route explain "fix login crash"
continuum team run default_dev_team "fix login crash"
continuum team run default_dev_team "fix login crash" --execute --allow-file src/login.py
```

Available editable starter presets:

```text
default_dev_team
local_only
review_heavy
fast_bugfix
research_then_code
```

No team is selected automatically by `continuum init`.

The preset file is written under:

```text
.continuum/teams/default_dev_team.json
```

## Default Development Team

```json
{
  "agents": {
    "explorer": {"provider": "gemini_cli", "can_edit_files": false},
    "reasoner": {"provider": "openrouter", "can_edit_files": false},
    "coder": {"provider": "claude_code", "can_edit_files": true},
    "tester": {"provider": "codex", "can_edit_files": true},
    "memory_worker": {"provider": "ollama", "can_edit_files": false}
  }
}
```

A bug-fix workflow maps to:

```text
explorer -> reasoner -> coder -> tester -> memory_worker
```

`team run` creates controlled tasks for the steps by default. Add `--execute`
to invoke enabled providers sequentially; each step receives a bounded context
packet and writes a bounded result message for the next role.

## Safety

- Ollama and OpenRouter cannot be configured as file editors or claim files.
- Automatically executed write roles require explicit `--allow-file` claims.
- Writable execution stops when existing or new dirty paths are outside claims.
- Automatic parallel routing remains future work; use explicit task worktrees
  and recorded review/test gates when isolating writers.

`continuum team run` remains non-executing unless `--execute` is supplied.
