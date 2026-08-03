# Benchmarks

Every figure on this page is generated from `benchmarks/results.json` by
`benchmarks/report.py`, which is the only way to keep a published number
and the run that produced it from drifting apart. Most of the faults this
benchmark has had were found by checking how a number was produced, not
by looking at the number.

Reproduce with:

```bash
python benchmarks/agent_memory_bench.py --trials 30 --agents claude,codex
python benchmarks/report.py --write
```

That calls real agent CLIs and spends quota. The context sizes below need
neither, and can be checked in seconds.

## What is asked

One project with a known recorded state, and five questions about it. The
probe kinds exist so that copying the context cannot score:

| Probe | Kind | claude | codex |
| --- | --- | --- | --- |
| `class` | distractor | 100% | 100% |
| `file` | recall | 100% | 100% |
| `count` | recall | 100% | 100% |
| `headroom` | inference | 100% | 100% |
| `owner` | unanswerable | 100% | 100% |

Percentages are per probe, on the injected arm.

## Context size

Deterministic and free to reproduce: no agent, no network, no quota.

|  | tokens | against raw |
| --- | --- | --- |
| raw event history | 1,710 |  |
| deep | 601 | 65% smaller |
| normal | 426 | 75% smaller |
| compact | 109 | 94% smaller |

## Accuracy

30 trials per cell, a fresh agent process each time. The interval is a
95% percentile bootstrap, so no distribution is assumed.

| Arm | claude | codex |
| --- | --- | --- |
| Continuum injects the context | 100% (100 to 100) | 100% (100 to 100) |
| No injection, the agent reads `.continuum/` itself | 100% (100 to 100) | 100% (100 to 100) |
| No project memory at all | 18% (15 to 20) | 20% (20 to 20) |

The middle row is the one that keeps this honest. An agent given no injected
context, but left free to open `.continuum/` itself, answers just as well.
Recording the context is what produces the accuracy. Injecting it is what
makes it cheap, which is the next table.

## Time to answer

| Arm | claude | codex |
| --- | --- | --- |
| Continuum injects the context | 4.9s | 17.9s |
| No injection, the agent reads `.continuum/` itself | 14.0s | 30.6s |
| No project memory at all | 19.8s | 106.7s |

## Trials that completed

An agent that fails to start has not answered badly, it has not answered.
Those trials are excluded from accuracy and counted here instead.

| Arm | claude | codex |
| --- | --- | --- |
| Continuum injects the context | 30/30 | 30/30 |
| No injection, the agent reads `.continuum/` itself | 29/30 | 30/30 |
| No project memory at all | 30/30 | 29/30 |

## Which source is used when they disagree

The injected context and the files on disk are made to contradict each
other: the injected version says the class is `BillingGateway`, the files
say `LedgerClient`.

|  | answered from injected context | answered from disk |
| --- | --- | --- |
| claude | 30/30 | 0/30 |
| codex | 30/30 | 0/30 |

## Categories

The distinctions LongMemEval draws, since recall alone flatters a memory
system.

| Category | claude | codex |
| --- | --- | --- |
| abstention | 30/30 | 30/30 |
| knowledge_update | 30/30 | 30/30 |
| multi_session | 30/30 | 30/30 |
| temporal | 30/30 | 30/30 |

## Delegation

One agent consulting another.

|  | round trip | reply delivered | completed |
| --- | --- | --- | --- |
| claude | 4.7s | 100% | 30/30 |
| codex | 17.2s | 100% | 30/30 |

## What this does not measure

One scenario, one project. A benchmark built from a single recorded state
cannot tell you how Continuum behaves across a real project's history.

Substring matching against accepted answers, per question. It is
reproducible and needs no judge, but it cannot tell a correct answer from a
differently-worded correct answer it was not told to accept.

Gemini is absent: it stops on a browser sign-in prompt before answering, so
there is nothing to measure without a signed-in machine.

## Faults this benchmark has had

Recorded because a benchmark that hides its own failures is worth less than
one that does not.

1. The scorer matched each accepted answer against the whole reply rather
   than the question it belonged to, so an answer to one question could
   satisfy another. Every accuracy figure it produced was withdrawn.
2. A conflict test built its injected prompt from the same files it was
   meant to contradict, so both sides agreed and the result meant nothing.
3. A control arm scored an agent's refusal to start as zero, turning an
   infrastructure failure into a model result.
4. Two runs were left calling the same CLIs at once, so a set of timings was
   measured under contention and had to be discarded and repeated.
5. The delegation arm scored every trial zero without reading the reply,
   and that zero was divided by the five fidelity probes, so a cell that
   never checked an answer published delegation as zero percent accurate
   over 30 trials, with a confidence interval to match.
6. The machine suspended for ten hours mid-run and charged the whole
   suspension to the trial in flight, moving one cell's mean from about
   forty seconds to 1217.5. Timings are now published as medians, and a
   cell whose longest trial exceeds twenty times its median is flagged
   rather than quietly dropped.
