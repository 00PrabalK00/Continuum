<p align="center">
  <img src="https://raw.githubusercontent.com/00PrabalK00/Continuum/main/continuum/ui/logo.png" alt="Continuum logo" width="240">
</p>

# Continuum

Git keeps the history of your code. Continuum keeps the working context around
it, on your machine, so you can switch AI without starting over.

## Does it actually work

<!-- benchmark-results:start -->

Measured against real agent CLIs, 30 trials per cell, on a project whose
recorded state we control. The interval is a 95% Wilson score interval.

| Arm | claude | codex |
| --- | --- | --- |
| Continuum injects the context | 100% (98 to 100) | 100% (98 to 100) |
| No injection, the agent reads `.continuum/` itself | 100% (98 to 100) | 100% (98 to 100) |
| No project memory at all | 17% (12 to 24) | 20% (14 to 27) |

Compact context for that project is 436 characters against 6,837 of
raw event history, 94% smaller. That is roughly 109 tokens against 1,710,
though the token figures are characters divided by four rather than real
tokenization, so the ratio is the exact part. It needs no agent and no API
key, and can be checked in seconds.

The middle row is the uncomfortable one, and it stays in the table. An agent
left to open `.continuum/` itself answers just as well, so recording the
context is what produces the accuracy. Injecting it is what makes it fast:

| Arm | claude | codex |
| --- | --- | --- |
| Continuum injects the context | 5.5s | 22.9s |
| No injection, the agent reads `.continuum/` itself | 17.1s | 29.9s |
| No project memory at all | 21.4s | 94.5s |

[docs/benchmarks.md](docs/benchmarks.md) has the method, the per-probe
breakdown, what this does not measure, and the faults this benchmark has had.

<!-- benchmark-results:end -->

## What that makes this

A recorder. The accuracy in that table comes from writing the context down, not
from anything Continuum does at the moment you open an agent — the middle row is
an agent with no daemon, no injection and no wrappers, reading the files cold,
and it scores the same. So the commands that matter are the ones that record:
`save`, `note`, `handoff`, and the history they build up.

Everything else is convenience on that. Injection makes it fast rather than
correct. Teams, worktrees, claims and evidence are there for coordinating
several agents at once, which is a different problem from a single agent
knowing where you left off. `continuum help --all` is grouped in that order.

## The problem

You are three hours into a bug. Your AI hits its context limit, or you switch
from Claude to Codex because one of them ran out, and the new session knows
nothing. You re-explain the codebase, the bug, what you already tried and which
two fixes did not work.

Continuum records that as you go and hands it to whichever AI you open next.

## Install

```bash
pip install git+https://github.com/00PrabalK00/Continuum.git
```

Then, in any project:

```bash
continuum install
```

That is the setup. Continuum finds the AI tools you already have, installs
itself into each one, and asks whether you also want meaning-based search and
automatic session summaries. Press enter to take the defaults, or run
`continuum install --yes` to skip the questions, which is also what happens when
no terminal is attached so scripts and CI keep working.

In Claude Code you can add it as a plugin instead, which registers the MCP
server for you:

```
/plugin marketplace add 00PrabalK00/Continuum
/plugin install continuum@continuum
```

The plugin still needs the `continuum` command on your PATH, since that is what
it runs. `continuum install` remains the way to reach Codex, Gemini and
everything else.

Now use your AI normally:

```
$ claude
> What am I working on?

Task: renamed payment client to BillingGateway.
Next: fix failing retry test.
```

Nobody told it. It already knew.

## Switching AI

When one runs out, open the next with your context already loaded:

```bash
continuum go
```

It picks an AI you were not just using, hands over your work, and saves an
updated note when the session ends. Name one if you would rather choose:

```bash
continuum go codex
```

Using ChatGPT or anything else in a browser? Put the context on your clipboard:

```bash
continuum copy
```

Forgot where you were? Run `continuum` on its own:

```
Continuum - my-project
Task: renamed the payment client to BillingGateway
Next: fix the failing retry test
Saved: 2 hours ago
```

## Git for context

Your code has a history you can inspect. Until now the context around it did
not, so when your AI believed something wrong you had no way to find out when
that started.

```bash
continuum log                    # checkpoints, when, against which commit
continuum diff C6 C9             # what changed between two of them
continuum blame BillingGateway   # where a claim entered, and if it is still current
continuum restore C6             # go back to an earlier state
```

Restoring appends rather than erases, the way `git revert` does and `git reset`
does not, so the log keeps saying what actually happened.

Each checkpoint records the commit it was written against, and reads report the
drift:

```
Recorded 37 days ago. Recorded against commit 7bab8ee, 14 commits ago.
Check it still describes the code before continuing from it.
```

Context goes stale in a way code cannot, so saying nothing would be the lie.
When your project is not a Git repository there is no commit to compare, and
Continuum reports the age alone rather than guessing.

## Saying which claims are settled

A decision and a hunch read the same once they are written down, so a guess
from Tuesday comes back on Friday sounding like something the project agreed.

```bash
continuum note decision "chose PostgreSQL over MySQL for the audit log"
continuum note hypothesis "the retry test probably fails on the timeout"
continuum note fact "the retry test asserts 3 attempts"
continuum note                   # list them
continuum note confirm 2         # or: continuum note drop 2
```

A hypothesis stays open until you confirm or drop it, and open ones reach the
next agent marked as unsettled:

```
Decisions: chose PostgreSQL over MySQL for the audit log
Open questions, not settled: the retry test probably fails on the timeout
```

Resolving one records that it was resolved rather than editing the original, so
the log still shows that somebody doubted it.

## Two AIs, two lines of context

Give each agent its own branch and they stop overwriting each other:

```bash
continuum branch codex-lane
continuum branch                 # list them, current one marked
continuum merge codex-lane       # bring it back
```

When both branches changed the same thing, the merge stops instead of letting
the most recent save win:

```
codex-lane and main both changed the same thing since they diverged.

Task
  main: renamed it to BillingGateway
  codex-lane: renamed it to LedgerClient

Nothing was recorded. Decide which is right and save it, or re-run
with --theirs to take codex-lane.
```

## Your AIs can ask each other things

```
You:     Ask Codex whether the retry fix is safe to ship.

Claude:  Codex says it is safe, but wants a regression test first.
```

Codex sees the same project context you do, so nothing needs re-explaining. It
works in both directions, and from a terminal:

```bash
continuum ask codex "is the retry fix safe to ship?"
```

Each AI has to be signed in on your machine first. Some sandbox their tools by
default, which can stop them starting another agent, and Continuum reports which
one is refusing rather than hanging.

## Any AI tool works

`claude`, `codex` and `gemini` work immediately. Anything else works as soon as
you name it:

```bash
continuum go hermes
continuum go opencode
```

Most AI tools take their prompt the same way, which is what Continuum assumes.
If yours is different, say so once:

```bash
continuum agent add myagent --inject flag --flag=--task
continuum agent add other --inject subcommand --subcommand run
continuum agent add piped --inject stdin
```

## Searching what you recorded

Ask in your own words. An exact phrase from the log is not required, so "which
database did we pick" finds the session where you chose PostgreSQL even though
neither word appears in the question.

```bash
continuum search "which database did we pick"
```

Word matching is ranked and always available, because SQLite provides it and
nothing needs installing. Meaning-based matching needs a local embedding model,
which `continuum install` offers to set up. Both run together, so an exact
wording match is still found when meaning-based results are also available.
Without a model, search works on wording alone and says so.

## How it works

Three layers, and the comparison to Git is structural rather than decorative.

Everything an AI does is appended to a log that is never rewritten: sessions
started, files changed, decisions recorded, errors hit. Handoffs are checkpoints
taken against that log, the way a commit is taken against your working tree.
`current.md` is the materialized view of the newest one, kept small enough to
hand to an AI without spending its context on your history.

| Git | Continuum |
| --- | --- |
| Repository | Project memory |
| Commit | Checkpoint |
| HEAD | `current.md` |
| Log | Decisions, attempts and outcomes |
| Branch | An agent's own line of context |
| Merge conflict | Two agents claiming different things |
| Blame | Where a claim came from |
| Signed tag | A decision, against a hypothesis nobody has resolved |
| Restore | Resume from an earlier state |

Everything stays on your machine, as plain files in your project:

```text
.continuum/
  current.md          # where things stand
  latest_handoff.md   # what the next AI gets
  events.sqlite3      # full history
  session_logs/       # what each AI actually did
```

Nothing is uploaded unless you deliberately configure a hosted model. Keep
`.continuum/` out of Git; Continuum adds the ignore rule for you. Session logs
contain whatever your AI saw, so read them before sharing.

## Limitations

Worth knowing before you rely on it.

Context sizes are estimated at four characters per token rather than tokenized.
Close enough to decide when to checkpoint, not close enough to quote.

Continuum cannot ask a provider how much quota you have left. It notices when an
agent says it has run out and remembers that across sessions, but it never shows
a percentage, because it does not have one.

Gemini needs its own sign-in before Continuum can drive it. Codex sandboxes its
tools by default, which can stop it starting another agent.

Merge compares the recorded task and next step. It does not reconcile the
underlying files, so two agents editing the same code still need the usual care.

The benchmark covers one project and one recorded state, with two agents. It
shows that a handoff arrives and gets used. It does not show how Continuum
behaves across a long real project.

## Contributing

```bash
git clone https://github.com/00PrabalK00/Continuum.git
cd Continuum
python -m pip install -e .
python -m unittest discover -s tests
```

The suite is standard-library `unittest` with no dependencies, and CI runs it on
Linux, macOS and Windows against Python 3.9 and 3.13. Continuum itself has no
runtime dependencies outside the standard library, and additions are expected to
keep it that way.

The benchmark is separate because it calls real agent CLIs and spends quota:

```bash
python benchmarks/agent_memory_bench.py --trials 30 --agents claude,codex
python benchmarks/report.py --write
```

The second command regenerates [docs/benchmarks.md](docs/benchmarks.md) and the
results block above. Neither is edited by hand, so any published figure can be
traced back to the run that produced it.

## More

Continuum also handles multi-agent teams, isolated Git worktrees, task routing,
governance rules and audit trails. You do not need any of it day to day.

```bash
continuum help --all
```

| I want to | Read |
| --- | --- |
| Start quickly | [Quickstart](docs/quickstart.md) |
| Understand how it works | [Getting started](docs/getting-started.md), [Architecture](docs/architecture.md) |
| See the measurements | [Benchmarks](docs/benchmarks.md) |
| Fix a problem | [Troubleshooting](docs/troubleshooting.md) |
| Set up MCP by hand | [MCP setup](docs/mcp-setup.md) |
| Use local or hosted models | [Providers](docs/providers.md) |
| Run AIs as a team | [Teams](docs/teams.md) |
| Keep parallel work separate | [Worktrees](docs/worktrees.md) |
| Check what an AI actually did | [Control center](docs/control-center.md) |
| See what changed | [Changelog](CHANGELOG.md) |

## License

MIT
