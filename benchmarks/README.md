# Benchmarks

`agent_memory_bench.py` measures whether a fresh agent continues work instead
of restarting it. It calls real agent CLIs, so it needs them installed and
signed in, and it spends quota.

```bash
python benchmarks/agent_memory_bench.py --trials 3 --agents claude,codex
```

Results are written to `results.json` next to the script. The numbers and the
caveats are written up in [../docs/benchmarks.md](../docs/benchmarks.md).

`results.json` in this directory is the raw output of one run. Two figures in
it are known to be wrong and are corrected in the write-up:

- `codex/no_memory` reports 0% in 0.6 seconds. Codex refuses to start outside a
  Git repository, so that arm recorded a refusal rather than a score. The
  harness now creates a repository for it; the corrected figure is 20.0%,
  measured in an isolated temporary directory.
- The category rows were collected before the compact-context fix that keeps
  earlier decisions in `current.md`.
