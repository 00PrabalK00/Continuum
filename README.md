<p align="center">
  <img src="continuum/ui/logo.png" alt="Continuum logo" width="240">
</p>

# Continuum

**Your AI forgets. Continuum remembers.**

You hit a context limit halfway through a task. Or you switch from Claude to
Codex because one ran out of quota. Either way you start over: re-explaining the
codebase, the bug, what you already tried.

Continuum keeps that context on your machine and hands it to whichever AI you
open next. Nothing leaves your computer.

Install it once and it becomes part of your agents — not another command you
have to remember:

```bash
continuum install
```

That finds the AI tools you already have and sets itself up inside each one.
After that, you just use your AI normally. It already knows where you left off.

---

## Install

You need Python 3.9+ and Git.

```bash
git clone https://github.com/00PrabalK00/Continuum.git
cd Continuum
python -m pip install -e .
```

Then, inside any project you work on:

```bash
continuum install
```

```
  + Claude Code        Continuum MCP server registered.
  + Claude Code skill  .claude/skills/continuum/SKILL.md
  + Claude Code hooks  .claude/settings.json
  + Codex              .codex/config.toml
  + Gemini CLI         .gemini/settings.json
  + Cursor             .cursor/rules/continuum.mdc
  = AGENTS.md          AGENTS.md

Your agents now read project context on their own. Nothing else to run.
```

It only touches tools you actually have, and it is safe to run again — use
`--dry-run` first if you want to see the plan.

### What it sets up

| Tool | What Continuum installs |
| --- | --- |
| Claude Code | MCP server, a skill, and session hooks |
| Codex | MCP server + `AGENTS.md` instructions |
| Gemini CLI | MCP server + `GEMINI.md` instructions |
| Cursor | an always-on rule file |
| Windsurf, Cline | a rule file |
| Anything else | `AGENTS.md`, which most agent CLIs already read |

For Claude Code the hooks are the important part: your context is loaded the
moment a session starts, and a handoff is written when it ends. You type
nothing.

```
$ claude
> What am I working on?

Task: renamed payment client to BillingGateway.
Next: fix failing retry test.
```

Nobody told it. It just knew.

Prefer not to install Continuum itself? Run it through npm:

```bash
npx -y github:00PrabalK00/Continuum install
```

---

## Driving it yourself

After `continuum install` you mostly never touch Continuum again. These are for
when you want to steer it directly.

### `continuum go` — work with an AI

Run it inside any project:

```bash
continuum go
```

It opens an AI agent with everything from your last session already loaded. When
you close the agent, Continuum writes down where you got to — automatically. You
never type a save command.

Name an agent if you want a specific one:

```bash
continuum go claude
continuum go codex
continuum go gemini
```

With no name, Continuum picks an agent you *didn't* just use — which is what you
want when you've hit a limit and are switching:

```
$ continuum go
Handing off to codex (claude ran last session).
```

### `continuum copy` — for AI without a CLI

ChatGPT in a browser, claude.ai, anything else:

```bash
continuum copy
```

Your context is printed and copied to the clipboard. Paste it into the chat and
carry on.

### Bonus: bare `continuum` — where was I?

```
$ continuum
Continuum - my-project
Task: renamed the payment client to BillingGateway
Next: fix the failing retry test in tests/test_billing.py
Saved: 2 hours ago
```

---

## A normal day

```bash
$ continuum go
Handing off to claude (first installed agent).
# ...you work for two hours, Claude hits its context limit...
Saved: renamed the payment client to BillingGateway
Next:  fix the failing retry test in tests/test_billing.py

$ continuum go
Handing off to codex (claude ran last session).
# Codex opens already knowing about BillingGateway and the failing test.
```

No copy-pasting. No "here's what we were doing" paragraph.

---

## Works with any AI tool

`claude`, `codex` and `gemini` work out of the box. **Any other command-line
agent works too** — just name it once:

```bash
continuum go hermes
continuum go opencode
continuum go my-internal-agent
```

Continuum remembers how to launch it. Most agent CLIs take their prompt as a
plain argument, which is what Continuum assumes. If yours is different, describe
it once:

```bash
# Agent expects a flag, e.g. myagent --task "..."
# Note the "=" — without it, argparse reads --task as another option.
continuum agent add myagent --inject flag --flag=--task

# Agent expects a subcommand, e.g. otheragent run "..."
continuum agent add otheragent --inject subcommand --subcommand run

# Agent reads its prompt from standard input
continuum agent add piped --inject stdin

# See what Continuum can reach
continuum agent list
```

---

## Let your AIs talk to each other

Continuum can put one AI's question to another and bring back the answer. Ask
Claude Code to consult Codex and it just works:

```
You:     Ask Codex whether the retry fix is safe to ship.

Claude:  (uses Continuum's ask_agent tool)
         Codex says the fix is safe but wants a regression test first.
```

Codex sees the same project context you do, so you don't have to explain
anything twice. It works in every direction — Codex can consult Claude the same
way — and the conversation is saved, so either AI can look it back up later.

From a terminal, the same thing:

```bash
continuum ask codex "is the retry fix safe to ship?"
```

Useful for a second opinion, delegating a side task, or getting an answer out of
an AI that still has quota left.

**Setup:** none — `continuum install` already wired this up.

### Two things to know

**Codex blocks this by default.** Codex runs its tools in a locked-down sandbox,
so *Codex asking someone else* is refused until you relax it for that run:

```bash
codex --sandbox danger-full-access
```

Codex *answering* others works normally. Continuum tells you when the sandbox is
the problem instead of showing a confusing permission error.

**Gemini needs signing in once.** Run `gemini` yourself in a terminal and finish
the browser login. Until then Continuum will report that Gemini is waiting on
authentication.

---

## Optional extras

**Write a note yourself.** Handoffs are automatic, but you can add one mid-session
— text after `|` becomes the next step:

```bash
continuum save "fixed the auth bug | next: test the retry logic"
```

**Let a small model write your handoffs.** Point Continuum at a local Ollama
model or a hosted one, and it summarises each session for you:

```bash
continuum handoff-llm set ollama llama3.1:8b
continuum handoff-llm set openrouter openai/gpt-4o-mini
continuum handoff-llm show
continuum handoff-llm off
```

If it ever fails, Continuum falls back to what it recorded — nothing breaks.

**See everything in a browser.**

```bash
continuum ui --open
```

**Mirror notes into Obsidian.**

```bash
continuum init --vault "/path/to/your/Obsidian Vault/Agents"
```

---

## Where your data lives

Everything is a file in your project, on your machine:

```text
.continuum/
  current.md          # a short summary of where things stand
  latest_handoff.md   # what to hand the next AI
  events.sqlite3      # full history
  session_logs/       # raw output from each agent session
  agents.json         # agent CLIs you've used here
```

Continuum keeps `.continuum/` out of Git for you. Nothing is uploaded anywhere
unless you deliberately configure a hosted model.

---

## Security

Worth reading before using Continuum on a sensitive project.

- `.continuum/` holds project memory and raw session logs. Keep it out of Git —
  Continuum adds the ignore rule, but check it survived.
- Review session logs before sharing them; agents echo whatever they were shown.
- API keys come from environment variables. Never put them in a config file.
- Hosted model calls are scanned for secrets before they leave the machine.
  Local models and local agents never send anything out.

---

## There's a lot more

Continuum also does multi-agent teams, isolated Git worktrees, task routing,
governance policy and audit evidence. None of it is needed for the daily loop,
so none of it is in your way. To see every command:

```bash
continuum help --all
```

| If you want to… | Read |
| --- | --- |
| Get going quickly | [Quickstart](docs/quickstart.md) |
| Understand the pieces | [Getting Started](docs/getting-started.md) · [Architecture](docs/architecture.md) |
| Fix something | [Troubleshooting](docs/troubleshooting.md) |
| Connect agents over MCP by hand | [MCP Setup](docs/mcp-setup.md) |
| Use local or hosted models | [Providers](docs/providers.md) |
| Run several agents as a team | [Teams](docs/teams.md) |
| Keep parallel work isolated | [Task Worktrees](docs/worktrees.md) · [Parallel Worktrees](docs/parallel-worktrees.md) |
| Track tasks and file ownership | [Tasks And Claims](docs/tasks-and-claims.md) |
| Review what an agent actually did | [Workflow Timeline](docs/workflow-timeline.md) · [Control Center](docs/control-center.md) |
| Bridge an agent you started yourself | [External Sessions](docs/external-sessions.md) |
| Run it as a background service | [Native Services](docs/services.md) · [Docker](docs/docker.md) |
| See what changed | [Changelog](CHANGELOG.md) · [Release Notes](docs/releases/README.md) |
| Know where it's heading | [Roadmap](docs/roadmap.md) · [Product Strategy](docs/product-strategy.md) |

---

## License

MIT
