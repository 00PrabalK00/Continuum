# Benchmarks

These numbers come from running real agent CLIs against a project whose state
we control, so every question has a known correct answer. The harness is not
part of the package; it lives outside the repository and is described here so
the results can be reproduced or disputed.

Read the caveats at the bottom before quoting anything from this page.

## What is measured

The categories follow the ones that agent memory systems publish against:
single-hop recall, knowledge update, temporal reasoning, multi-session recall,
and abstention. LoCoMo and LongMemEval define them.

Five probe questions describe one recorded project state. A renamed class, a
failing test file, an assertion count, what happened to the callers, and the
next action. All five are answerable from the recorded handoff and from nothing
else, so a correct answer means the handoff arrived.

## Context size

One scenario, 40 background events on top of the handoff.

| | tokens | against raw |
| --- | --- | --- |
| raw event history | 1,604 | |
| deep | 608 | 62% smaller |
| normal | 412 | 74% smaller |
| compact | 74 | 95% smaller |

## Fidelity

Five probes, three trials per arm, one fresh agent process per trial.

| Arm | Claude | Codex |
| --- | --- | --- |
| Continuum injects the context | 100%, SD 0.00, 6.5s | 100%, SD 0.00, 37.6s |
| No injection, `.continuum/` readable | 100%, SD 0.00, 29.1s | 100%, SD 0.00, 49.0s |
| No project memory at all | 13.3%, SD 0.47, 20.2s | 20.0%, SD 0.00, 108.0s |

The injected prompt is 286 tokens. The other two arms send 82 and let the agent
find what it needs.

The second row is the uncomfortable one. An agent with no injected context
scores the same, because it opens `.continuum/` itself. Recording the files is
what produces the accuracy. Injecting them is what makes it fast: 6.5 seconds
against 29.1 for Claude, 37.6 against 49.0 for Codex.

## Which source does the agent actually use

Injected context and the files on disk were made to disagree. The injected
version said the class was renamed to `BillingGateway`; the files said
`LedgerClient`.

| | answered from injected context | answered from disk |
| --- | --- | --- |
| Claude | 3/3 | 0/3 |
| Codex | 3/3 | 0/3 |

Injection wins when the two conflict, for both agents.

## Categories

Three trials each, both agents, after the compact-context fix.

| Category | Claude | Codex |
| --- | --- | --- |
| Knowledge update: a superseded fact is dropped | 3/3 | 3/3 |
| Temporal: which of two events came first | 3/3 | 3/3 |
| Multi-session: a decision 25 events back | 3/3 | 3/3 |
| Abstention: says UNKNOWN rather than inventing | 3/3 | 3/3 |

Multi-session recall used to be the weak one. Claude scored 2/3 before the fix,
and the passing answers cited `.continuum/events.jsonl`, meaning the agent had
gone and read the raw log because the compact context no longer mentioned the
decision. `current.md` now carries a short trail of earlier handoff tasks, and
compact context for that case is 44 tokens rather than 74. A six-trial rerun
scored 6/6.

Codex passed that category at 3/3 even before the fix. It reads files more
aggressively, which is also why it is slower.

## Delegation

One agent consulting another, three trials, 210 prompt tokens.

| | round trip |
| --- | --- |
| Claude | 7.3s |
| Codex | 24.7s |

## Caveats

Three trials per cell. There are no confidence intervals here and the floor is
visibly noisy: Claude's no-memory arm scored 26.7% in one run and 13.3% in the
next.

Scoring is substring matching against accepted answers. An agent that echoes
the injected text without understanding it still passes.

One scenario, one project, two agents. Gemini is not included because it stops
on a browser sign-in prompt before answering.

Two earlier versions of this harness produced numbers that were wrong, and both
failures are worth knowing about because they are easy to repeat:

The first conflict test built the injected prompt by calling
`resume_context()`, which reads the same files it was supposed to contradict.
Injected and disk therefore said the same thing, and the result meant nothing.

Codex's no-memory arm first reported 0% in 0.3 seconds. Codex refuses to start
outside a Git repository, so a refusal was recorded as a score of zero. After
adding a repository it ran, but scored 5/5 once in an empty project, because
the control directory sat next to the other test projects and Codex spent 156
seconds finding them. The 20.0% above comes from an isolated temporary
directory.
