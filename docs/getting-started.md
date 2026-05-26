# Getting Started

## Release Boundary

```text
v0.1 proves Continuum can preserve context across agents.
v0.2 proves Continuum can automatically orchestrate agents.
```

Continuum v0.2 retains local project memory and compact handoffs, then adds
explicit sequential workflows. `team run` plans only; `team run --execute`
launches enabled providers in route order with bounded context packets and
file-claim checks for writer roles.

## Source Install

```bash
git clone https://github.com/00PrabalK00/Continuum.git
cd Continuum
python -m pip install .
continuum --version
```

Or exercise the npm entrypoint from the checkout:

```bash
npm install --global .
continuum --version
```

The target registry experience, after npm publishing is completed, is:

```bash
npx -y continuum-agent-memory@latest init
npx -y continuum-agent-memory@latest up
npx -y continuum-agent-memory@latest ui --open
```

Publishing and fresh-machine timing validation are tracked in
[issue #8](https://github.com/00PrabalK00/Continuum/issues/8).

## Initialize A Repository

```bash
cd /path/to/your/project
continuum init
continuum doctor
continuum up
continuum status
```

Add Obsidian mirroring only when required:

```bash
continuum init --vault "/path/to/Obsidian Vault/Agents"
```

Proceed to [the demo](demo.md) to hand work from Claude to Gemini to Codex
without reloading the entire session history, then try opt-in sequential
execution with a configured team.
