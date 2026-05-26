# Continuum Teams

Continuum Teams lets a project configure role-based AI workflows in JSON.

```bash
continuum team init default_dev_team
continuum team show default_dev_team
continuum route explain "fix login crash"
continuum team run default_dev_team "fix login crash"
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

`team run` creates controlled tasks for the steps. It does not execute the
providers automatically in `v0.1`.

## Safety

- Ollama and OpenRouter cannot be configured as file editors by default.
- Write work must use task file claims before concurrent routing is enabled.
- Team workflow execution and Git worktree isolation remain future adapters.

`continuum team run` prints the plan and creates controlled task records only.
It never launches providers in this version.
