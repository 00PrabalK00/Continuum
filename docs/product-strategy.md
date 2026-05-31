# Product Strategy

Continuum is the local control plane for coordinating AI coding agents across
real software projects.

Its product promise is simple: keep every AI agent in sync, in scope and under
review. Continuum is not another model, IDE, autocomplete tool or generic agent
framework. It coordinates Claude Code, Codex, Gemini CLI, Ollama, OpenRouter and
similar tools through shared memory, bounded context, file ownership, Git
worktree isolation, workflow visibility and review gates.

## Market Signal

Developer AI adoption is high, but trust is still low. The useful product
direction is not "more AI"; it is AI work that developers can inspect, constrain
and recover from.

That means Continuum should sell:

- Less repeated context.
- Safer delegation.
- Human review gates.
- Better logs.
- Better rollback.
- Better proof that an agent did what it claims.

AI agents are also still early in mainstream development workflows. That is good
for Continuum because the category is not settled. The wedge is not "replace
Claude Code" or "replace Codex." The wedge is: who coordinates them, remembers
the project, manages file ownership, handles handoffs and gives the developer
one safe cockpit?

Continuum should position itself as Agent Ops for software engineering, or more
plainly, an AI engineering control plane.

## Customer Requirements

The strongest product requirements are accuracy, security, privacy, workflow fit
and cost control.

- Accuracy requires test evidence, verification and review gates.
- Security requires file claims, allowlists, command policies, secret scanning
  and MCP trust controls.
- Workflow fit requires Git, terminal, IDE, MCP and CI compatibility.
- Learning curve requires presets, visual workflows and simple commands.
- Cost pressure requires model routing, compaction and local model support.

The biggest market gap is team coordination. Most tools optimize the solo prompt
loop. Continuum should answer team-level questions from real scheduling state:

- Who is working on what?
- Which agent owns which file?
- What context did it receive?
- What tests did it run?
- What changed since approval?
- Which task is blocked?
- Which agent handed off to whom?
- Which output is final?

## Technical Direction

The winning architecture is MCP plus policy plus audit plus Git isolation plus
human gates. MCP is useful plumbing, but it is not enough by itself because tools
can represent arbitrary code execution and must be governed by consent,
visibility and authorization.

Continuum should build deterministic coordination around nondeterministic
agents:

- Agents can be messy; Continuum records evidence.
- Agents can over-edit; Continuum enforces claims.
- Agents can lose context; Continuum rebuilds packets.
- Agents can be expensive; Continuum routes work intelligently.
- Agents can sound confident; Continuum verifies against Git, tests, policy,
  event logs and human approvals.

The source of truth should be Git state, filesystem state, test output, event
logs, policy files, context packets, review gates and explicit human approval.
The model can propose; Continuum should verify.

## Core Objects

Continuum should organize the product around these objects:

- Project.
- Agent.
- Provider.
- Task.
- Context packet.
- File claim.
- Worktree lane.
- Workflow graph.
- Evidence pack.
- Policy.
- Review gate.
- Test gate.
- Handoff.
- Memory item.
- Audit event.

Evidence packs and policy should become first-class objects alongside the
existing memory, task, claim and worktree primitives.

## Killer Workflow

The daily workflow should be:

```bash
continuum init
continuum doctor
continuum objective "Add Stripe billing"
```

Continuum should then produce a plan, worktrees, file claims, context packets,
timeline state and next commands. Claude can implement the backend, Codex can
write tests, Gemini can review docs and edge cases, and Continuum blocks merge
until test and review evidence pass.

The PR packet should include the final summary, changed files, agent
contributions, test evidence, review evidence, known risks and rollback notes.

## Shipped Strategy Primitives

The current codebase already includes several strategy primitives that should
be treated as product surface, not only future ideas:

- `continuum objective`: one-command planning for a coordinated multi-agent
  objective, with optional worktree scheduling.
- `continuum evidence`: task evidence aggregation across claims, changed files,
  worktree state, test gates, review gates, risks and next action.
- `continuum pr-packet`: reviewer-facing markdown packet generation from task
  evidence.
- `continuum flight-record`: replayable Agent Flight Recorder output for a task,
  built from evidence, context, messages, events and handoff state.
- `continuum context enrich`, `continuum context diff` and `continuum context
  score`: symbol-aware context inspection, packet comparison and context quality
  scoring.
- `continuum policy`, `continuum command classify`, `continuum secrets scan`,
  `continuum audit export` and `continuum mcp trust`: governance controls for
  provider policy, shell-command risk, secret redaction, audit export and MCP
  trust.
- `continuum roi`: engineering evidence for tasks completed, failed tasks,
  token estimates, out-of-scope edits, tests, review rejections, reruns, manual
  corrections and cost-aware routing guidance.
- `continuum benchmark capture` and `continuum benchmark compare`: a local
  with-vs-without Continuum harness for tokens, failed attempts, scope control,
  tests run, context resets, human corrections and merge readiness.
- Control Center Runs trust layer: workflow timeline, multi-agent worktree
  board, Agent Flight Recorder cards and Context Packet Studio table backed by
  real task, worktree, context and evidence state.

## Remaining Hardening

These roadmap issues remain the execution plan for the next layer of hardening:

- [#31 Symbol-Aware Context Builder](https://github.com/00PrabalK00/Continuum/issues/31):
  deepen the existing context enrichment into automatic packet construction.
- [#32 Context Diff And Score](https://github.com/00PrabalK00/Continuum/issues/32):
  expand the current CLI diff and score into stored, inspectable packet metadata.
- [#33 MCP Trust Registry](https://github.com/00PrabalK00/Continuum/issues/33):
  continue hardening the shipped trust registry and connect it to more MCP tool
  boundaries.
- [#36 One-Command Objective Flow](https://github.com/00PrabalK00/Continuum/issues/36):
  improve the shipped objective planner into the primary demo flow.

## Avoid

Continuum should avoid becoming a generic agent framework. Stay focused on
software engineering, Git, terminal workflows, MCP, coding agents, review gates
and context continuity.

It should also avoid fake autonomy. The trustworthy pitch is controlled parallel
agent work, explicit human review, clear file ownership and evidence before
merge.

Finally, avoid integrating too many providers early. Nail the workflow with
Claude Code, Codex, Gemini CLI, Ollama and OpenRouter before expanding.
