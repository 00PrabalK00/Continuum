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

## The results file

`results.json` is the run every published figure comes from. The pre-scorer-fix
file that used to sit here has been deleted rather than kept alongside it: two
results files in one directory, one of them withdrawn, is how a withdrawn number
finds its way back onto a page.

## Tests

`tests/test_benchmark_harness.py` covers the harness faults, including the case
that made this worth catching: a reply with every answer against the wrong
question scored 5/5 before and scores 0/5 now.

`tests/test_benchmark_report.py` covers the generator, which is the other place
an untrue number can reach a published page.
