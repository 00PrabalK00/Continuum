<p align="center">
  <img src="https://raw.githubusercontent.com/00PrabalK00/Continuum/main/continuum/ui/logo.png" alt="Continuum logo" width="240">
</p>

# Continuum

Your AI runs out of context. Or you switch from Claude to Codex because one hit
its limit. Either way you start over, re-explaining the codebase, the bug, and
what you already tried.

Continuum saves that context on your machine and gives it to whichever AI you
open next.

---

## Get started

```bash
git clone https://github.com/00PrabalK00/Continuum.git
cd Continuum
python -m pip install -e .
```

Then, in any project you work on:

```bash
continuum install
```

That is the setup. Continuum finds the AI tools you already have, installs
itself inside each one, then asks whether you also want meaning-based search and
automatic session summaries. Say yes and it does the rest, including downloading
a local embedding model if you want one.

Answer nothing and press enter to take the defaults. `continuum install --yes`
skips the questions, which is also what happens when there is no terminal
attached, so scripts and CI keep working.

Now use your AI the way you normally would:

```
$ claude
> What am I working on?

Task: renamed payment client to BillingGateway.
Next: fix failing retry test.
```

Nobody told it. It already knew.

---

## Switching AI

When one AI runs out, open the next one with your context already loaded:

```bash
continuum go
```

It picks an AI you were not just using, hands over your work, and saves an
updated note when you are done.

Want a specific one? Name it:

```bash
continuum go claude
continuum go codex
continuum go gemini
```

Using ChatGPT or another AI in a browser? Copy your context to the clipboard
and paste it in:

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

---

## Your AIs can ask each other things

```
You:     Ask Codex whether the retry fix is safe to ship.

Claude:  Codex says it's safe, but wants a regression test first.
```

Codex sees the same project context you do, so nothing needs re-explaining. It
works both ways, and from a terminal too:

```bash
continuum ask codex "is the retry fix safe to ship?"
```

Each AI needs to be signed in on your machine before Continuum can reach it,
and some sandbox their tools by default, which can stop them starting another
agent. Continuum reports which one is refusing and why rather than hanging.

---

## Any AI tool works

`claude`, `codex` and `gemini` work immediately. Anything else works as soon as
you name it:

```bash
continuum go hermes
continuum go opencode
```

Most AI tools take their prompt the same way, which is what Continuum assumes.
If yours is different, tell it once:

```bash
continuum agent add myagent --inject flag --flag=--task     # myagent --task "..."
continuum agent add other --inject subcommand --subcommand run   # other run "..."
continuum agent add piped --inject stdin                     # reads from stdin
continuum agent list
```

---

## Does it actually work

<!-- benchmark-results:start -->

**The figures that were here have been withdrawn while they are re-measured.**
Review found that the scorer matched each accepted answer against the whole
reply rather than against the question it belonged to, so an answer to one
question could satisfy an unrelated one and a reply that skipped a question
entirely could still score full marks. Any accuracy number it produced is
unreliable, in an unknown direction.

A re-measurement is underway with per-question scoring, adversarial and
unanswerable probes, 30 trials per cell and confidence intervals.
[docs/benchmarks.md](docs/benchmarks.md) records the method and what went wrong.

<!-- benchmark-results:end -->

---

## Running a team of AIs

One AI can also plan and run a whole sequence of others. Ask for it in
conversation:

```
You:     Use Continuum to plan a workflow for fixing the retry test.

Claude:  Planned W0001 on team local_agent_team.
         1. tester via codex
         Run it and codex takes the step.
```

Teams are named sets of roles, each bound to a provider, so `local_agent_team`
might send exploration to Gemini, implementation to Claude and testing to Codex.
List them with `continuum team list` and install one with
`continuum team init <name>`.

Planning never runs anything. Running a workflow needs an explicit list of files
the writing roles may edit, and a step that touches anything else stops the
workflow. From a terminal:

```bash
continuum team run local_agent_team "fix the retry test"
continuum team run local_agent_team "fix the retry test" --execute --allow-file src/retry.py
```

---

## Searching what you recorded

Agents look things up in your project memory by meaning as well as by wording,
so "which database did we pick" finds the session where you chose PostgreSQL
even though neither word appears in the question.

```bash
continuum search "which database did we pick"
```

Word matching is ranked and always available, because SQLite provides it and
nothing needs installing. Meaning-based matching needs a local embedding model,
which `continuum install` offers to set up for you. Both are used together and
neither crowds the other out, so an exact wording match is still found when
meaning-based results are also available. Without a model, search works on
wording alone and says so.

---

## Nice to have

Write a note yourself, any time. Text after `|` is the next step:

```bash
continuum save "fixed the auth bug | next: test the retry logic"
```

Have a small model write your notes for you:

```bash
continuum handoff-llm set ollama llama3.1:8b
```

See everything in a browser:

```bash
continuum ui --open
```

---

## Your data

Everything stays on your machine, as plain files in your project:

```text
.continuum/
  current.md          # where things stand
  latest_handoff.md   # what the next AI gets
  events.sqlite3      # full history
  session_logs/       # what each AI actually did
```

Nothing is uploaded unless you deliberately set up a hosted model. Keep
`.continuum/` out of Git; Continuum adds the ignore rule for you. Session logs
contain whatever your AI saw, so read them before sharing.

---

## There is more if you want it

Continuum also handles multi-agent teams, isolated Git worktrees, task routing,
governance rules and audit trails. You do not need any of it for everyday use.

```bash
continuum help --all
```

| I want to | Read |
| --- | --- |
| Start quickly | [Quickstart](docs/quickstart.md) |
| Understand how it works | [Getting Started](docs/getting-started.md), [Architecture](docs/architecture.md) |
| See the measurements | [Benchmarks](docs/benchmarks.md) |
| Fix a problem | [Troubleshooting](docs/troubleshooting.md) |
| Set up MCP by hand | [MCP Setup](docs/mcp-setup.md) |
| Use local or hosted models | [Providers](docs/providers.md) |
| Run AIs as a team | [Teams](docs/teams.md) |
| Keep parallel work separate | [Worktrees](docs/worktrees.md), [Parallel Worktrees](docs/parallel-worktrees.md) |
| Track tasks and file ownership | [Tasks And Claims](docs/tasks-and-claims.md) |
| Check what an AI actually did | [Workflow Timeline](docs/workflow-timeline.md), [Control Center](docs/control-center.md) |
| Run it as a background service | [Services](docs/services.md), [Docker](docs/docker.md) |
| See what changed | [Changelog](CHANGELOG.md) |
| Cut a release | [Releasing](docs/releasing.md) |
| See what is next | [Roadmap](docs/roadmap.md) |

---

## License

MIT
