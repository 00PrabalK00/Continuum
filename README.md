<p align="center">
  <img src="continuum/ui/logo.png" alt="Continuum logo" width="240">
</p>

# Continuum

**Your AI forgets. Continuum remembers.**

Your AI runs out of context. Or you switch from Claude to Codex because one hit
its limit. Either way you start over — re-explaining the codebase, the bug, what
you already tried.

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

That's it. Continuum finds the AI tools you already have and sets itself up
inside each one.

Now just use your AI normally:

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

It picks an AI you weren't just using, hands over your work, and saves an
updated note when you're done.

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

Forgot where you were? Just run:

```bash
continuum
```

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

Two things to know:

- **Gemini** needs you to sign in once. Run `gemini` in a terminal and finish
  the browser login.
- **Codex** can answer other AIs fine, but for Codex to *ask* others you need to
  start it with `codex --sandbox danger-full-access`. That's Codex's rule, not
  ours.

---

## Any AI tool works

`claude`, `codex` and `gemini` work immediately. Anything else works as soon as
you name it:

```bash
continuum go hermes
continuum go opencode
```

Most AI tools take their prompt the same way, which Continuum assumes. If yours
is different, tell it once:

```bash
continuum agent add myagent --inject flag --flag=--task           # myagent --task "..."
continuum agent add other --inject subcommand --subcommand run    # other run "..."
continuum agent add piped --inject stdin                          # reads from stdin
continuum agent list
```

---

## Nice to have

Write a note yourself, any time — text after `|` is the next step:

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
`.continuum/` out of Git — Continuum adds the ignore rule for you. Session logs
contain whatever your AI saw, so read them before sharing.

---

## There's more if you want it

Continuum also handles multi-agent teams, isolated Git worktrees, task routing,
governance rules and audit trails. You don't need any of it for everyday use.

```bash
continuum help --all
```

| I want to… | Read |
| --- | --- |
| Start quickly | [Quickstart](docs/quickstart.md) |
| Understand how it works | [Getting Started](docs/getting-started.md) · [Architecture](docs/architecture.md) |
| Fix a problem | [Troubleshooting](docs/troubleshooting.md) |
| Set up MCP by hand | [MCP Setup](docs/mcp-setup.md) |
| Use local or hosted models | [Providers](docs/providers.md) |
| Run AIs as a team | [Teams](docs/teams.md) |
| Keep parallel work separate | [Worktrees](docs/worktrees.md) · [Parallel Worktrees](docs/parallel-worktrees.md) |
| Track tasks and file ownership | [Tasks And Claims](docs/tasks-and-claims.md) |
| Check what an AI actually did | [Workflow Timeline](docs/workflow-timeline.md) · [Control Center](docs/control-center.md) |
| Run it as a background service | [Services](docs/services.md) · [Docker](docs/docker.md) |
| See what changed | [Changelog](CHANGELOG.md) |
| See what's next | [Roadmap](docs/roadmap.md) |

---

## License

MIT
