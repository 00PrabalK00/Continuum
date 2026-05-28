# Parallel Worktree Scheduling

Continuum can plan isolated Git worktrees for independent agent lanes. This
lets several agents work on separate parts of a repository without sharing the
same writable checkout.

## Plan Lanes

```bash
continuum worktree schedule "Build auth flow with tests and docs" \
  --lane backend:claude:src/auth,src/api \
  --lane tests:codex:tests \
  --lane docs:gemini:docs \
  --depends-on tests:backend
```

Lane format:

```text
role:agent:path1,path2
```

Supported lane agents are `claude`, `codex` and `gemini`. Paths must be
project-relative. Continuum rejects overlapping ownership such as `src` and
`src/api` in different lanes.

If no `--lane` values are provided, Continuum tries a conservative repo scan
for common folders such as backend, UI, tests and docs. If it cannot infer a
safe split, it prints an exact `--lane` example.

## Run A Lane

```bash
continuum worktree resume T0001 claude compact
continuum worktree resume T0002 codex compact --interactive
```

The resume command uses the root project memory store and launches the agent
with its current working directory set to the task worktree. Each lane receives
a compact context packet with objective, owned paths, branch, dependencies and
merge gates.

## Track And Merge

```bash
continuum worktree schedules
continuum worktree schedule-status P0001
continuum worktree diff T0001
continuum worktree test-result T0001 --pass --note "pytest tests/auth"
continuum worktree review T0001 --approve --note "reviewed diff"
continuum worktree merge T0001
```

Merge requires a passing test gate, approved review gate, no uncommitted
worktree changes, no worktree commits after recorded gates, dependency lanes
merged or marked done, and a clean main working tree.

Continuum does not automatically launch parallel providers in this release. It
prepares safe worktree lanes and leaves execution explicit.
