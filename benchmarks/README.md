# Benchmarks

`agent_memory_bench.py` measures whether a fresh agent continues work instead
of restarting it. It calls real agent CLIs, so it needs them installed and
signed in, and it spends quota.

```bash
python benchmarks/agent_memory_bench.py --trials 3 --agents claude,codex
```

Results are written to `results.json` next to the script. The numbers and the
caveats are written up in [../docs/benchmarks.md](../docs/benchmarks.md).

`results.json` in this directory is the raw output of one run, kept as a record
of what the harness used to produce. **Its accuracy figures should not be
quoted.** They were scored by matching each accepted answer against the whole
reply rather than against the question it belonged to, so an answer to one
question could satisfy another and a reply that skipped a question could still
score full marks. That is fixed here; the numbers in the file predate the fix.

Two further faults in that file:

- `codex/no_memory` reports 0% in 0.6 seconds. Codex refuses to start outside a
  Git repository, so that arm recorded a refusal rather than a score. A
  non-zero exit is now reported as an incomplete trial and kept out of the
  accuracy denominator.
- The category rows were collected before the compact-context fix that keeps
  earlier decisions in `current.md`.

`tests/test_benchmark_harness.py` covers all three faults, including the case
that made this worth catching: a reply with every answer against the wrong
question scored 5/5 before and scores 0/5 now.
