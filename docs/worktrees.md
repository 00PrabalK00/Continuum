# Task Worktrees

Use task-bound worktrees before parallel writer activity:

```bash
continuum task create "Patch auth" --mode parallel
continuum worktree create T0001
continuum worktree diff T0001
continuum worktree test-result T0001 --pass --note "python -m unittest: passed"
continuum worktree review T0001 --approve --note "Diff reviewed; scoped and safe."
continuum worktree merge T0001
```

Each worktree has a dedicated `continuum/t0001` branch and local metadata for
changed files, diff summary, recorded test result, completion note and
rollback branch command.

`merge` refuses to proceed until passing tests and review approval are recorded
and the main worktree is clean. Remove abandoned isolated work with:

```bash
continuum worktree discard T0001 --force
```
