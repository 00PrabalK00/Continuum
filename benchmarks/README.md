# Benchmarks

`agent_memory_bench.py` measures whether a fresh agent continues work instead
of restarting it. It calls real agent CLIs, so it needs them installed and
signed in, and it spends quota.

```bash
python benchmarks/agent_memory_bench.py --trials 30 --agents claude,codex
python benchmarks/report.py --write
```

The first command writes `results.json` next to the script. The second turns it
into [../docs/benchmarks.md](../docs/benchmarks.md) and the results block in the
top-level README. Nothing on either page is typed by hand, because three of the
four faults this benchmark has had were found by checking a published number
against the run that produced it, and a hand-copied table is where that check
stops being possible.

`report.py` refuses to render a results file whose `schema` does not match the
harness, so an older run cannot be published as a current result.

## The file kept in this directory

`results.json` here is the raw output of one earlier run, kept as a record of
what the harness used to produce. **Nothing in it should be quoted.** It
predates the current scorer and the current probe set, and `report.py` will
refuse to render it.

Its accuracy figures were scored by matching each accepted answer against the
whole reply rather than against the question it belonged to, so an answer to one
question could satisfy another and a reply that skipped a question could still
score full marks. Its `codex/no_memory` row reports 0% in 0.6 seconds, which was
Codex refusing to start outside a Git repository rather than Codex answering
badly. Its category rows were collected before the compact-context fix that
keeps earlier decisions in `current.md`.

## Tests

`tests/test_benchmark_harness.py` covers the harness faults, including the case
that made this worth catching: a reply with every answer against the wrong
question scored 5/5 before and scores 0/5 now.

`tests/test_benchmark_report.py` covers the generator, which is the other place
an untrue number can reach a published page.
