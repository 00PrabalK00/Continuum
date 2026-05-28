# Workflow Timeline And Agent Scheduling

Continuum should include a workflow view that shows how different models,
agents and tools execute tasks over time.

The view is a mix of:

- Gantt chart
- Agent timeline
- Task dependency graph
- Conversation flow map
- Shared context tracker

## Purpose

The timeline makes multi-model orchestration understandable. Users should see
how work is split, how agents communicate, how context moves through the
system and where approval or blocked work needs attention.

Example:

```text
Claude handles implementation.
GPT handles reasoning and review.
Gemini explores alternate approaches.
Ollama summarizes logs locally.
Continuum coordinates shared memory, tasks, claims and scheduling state.
```

## Timeline Lanes

Each lane represents a model, agent or worker:

```text
Claude Code
Codex
Gemini CLI
OpenRouter reasoning model
Ollama memory worker
Continuum scheduler
User approval
```

Tasks appear as blocks on those lanes. Each block should show:

- Task ID and title
- Assigned agent or provider
- Status
- Start and end time
- Dependency links
- Claimed or changed files
- Context packet IDs
- Handoff points
- Approval state
- Test or review gate state

## Required States

The timeline should display:

- Running tasks
- Completed tasks
- Blocked tasks
- Failed tasks
- Tasks waiting for user approval
- Parallel worktree lanes
- Handoffs between agents
- Final output producer

## Data Sources

Do not create separate timeline-only state. Build the view from existing
Continuum primitives:

- SQLite events
- Workflow records
- Workflow messages
- Controlled tasks
- File claims
- Worktree lanes
- Context packets
- Handoff files
- Provider result messages
- Test and review gates

## Scheduling Boundary

The timeline is a view of real scheduling state. It must not show fake agent
activity, fake productivity metrics or speculative execution as if it already
happened.

Planned work may be shown as planned. Running work may be shown as running
only when Continuum has created the task, lane or workflow step and recorded
the event.

## UI Shape

The Control Center should expose this as a Workflow Timeline page or a
timeline panel inside Runs.

Recommended layout:

```text
Left: agent lanes
Center: time-based task blocks
Right: selected task inspector
Bottom: event stream and handoff/context packet details
```

The inspector should show:

- Current task
- Assigned agent
- Dependencies
- Claimed files
- Changed files
- Context packets used
- Memory packets created
- Latest handoff
- Approval or blocker reason
- Next action

## Success Criteria

The feature is ready when a user can answer these questions without opening
raw logs:

- Who is working on what?
- What is blocked?
- What depends on what?
- Which files are owned by each agent?
- Which context packet did this agent receive?
- Which agent handed off to the next agent?
- Which work is parallel and isolated in worktrees?
- Which output is final?
