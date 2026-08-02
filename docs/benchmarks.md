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

## Fidelity: withdrawn, being re-measured

The accuracy figures previously reported here are withdrawn.

The scorer searched for each accepted answer across the entire reply rather than
against the numbered question it belonged to. An answer to question 3, "the
retry test asserts 3 attempts", satisfied the unrelated question 5 probe through
the word "retry". A reply that answered some questions and skipped others could
therefore score full marks, and a reply with every answer against the wrong
question could too.

That flaw affects every accuracy number in the previous version of this page, in
an unknown direction. They are not reproduced here, because a number that cannot
be trusted is worse than no number.

Two measurements from the same run do not depend on the scorer, because they are
wall-clock timings rather than judgements about correctness:

| Arm | Claude | Codex |
| --- | --- | --- |
| Continuum injects the context | 6.5s | 37.6s |
| No injection, `.continuum/` readable | 29.1s | 49.0s |

The injected prompt is 286 tokens. The other arms send 82 and let the agent find
what it needs, which is what the extra 20 to 30 seconds buys.

The re-measurement fixes the scorer to match per question, and additionally
adds probes that cannot be satisfied by echoing the context: inference probes
whose answer is not a literal span, distractor probes where a plausible wrong
answer appears verbatim, and unanswerable probes where the correct response is
to say the context does not contain it. It runs 30 trials per cell with
bootstrap confidence intervals, and reports the completion rate separately so an
agent that fails to start is not scored as an agent that answered badly.

## Which source does the agent actually use

Also unaffected by the scoring flaw: this asks one question and reads one word
out of the reply.

Injected context and the files on disk were made to disagree. The injected
version said the class was renamed to `BillingGateway`; the files said
`LedgerClient`.

| | answered from injected context | answered from disk |
| --- | --- | --- |
| Claude | 3/3 | 0/3 |
| Codex | 3/3 | 0/3 |

Injection wins when the two conflict, for both agents.

## Categories

These are not affected by the scoring flaw above. Each category asks a single
question and checks that one reply, so there is no second probe for an answer to
satisfy by accident. Their weaknesses are the ordinary ones: three trials, and
matching on substrings, so an agent that echoes the context without
understanding it still passes.

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

Three trials per cell, and no confidence intervals. The floor was visibly noisy
even before the scoring flaw was found: the same no-memory arm scored 26.7% in
one run and 13.3% in the next, which is on its own enough reason not to quote a
single figure from three trials.

Scoring is substring matching against accepted answers. An agent that echoes
the injected text without understanding it still passes. Worse, in the five
probe fidelity run the match was made against the whole reply rather than the
question it belonged to, which is why those numbers are withdrawn above.

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
seconds finding them. That arm was re-run in an isolated temporary directory,
though its accuracy figure is withdrawn along with the rest.

Three separate instrument faults, each found only by looking at how a result was
produced rather than at the result itself. That is the reason this page exists.
