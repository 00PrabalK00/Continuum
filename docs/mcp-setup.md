# MCP Setup

Continuum exposes bounded, project-scoped memory over stdio:

```bash
continuum mcp serve --project "/path/to/project"
```

Configure that command as an MCP stdio server in Claude Code, Gemini CLI or
Codex. Verify whether a Continuum reference is visible in the common local
agent configuration files with:

```bash
continuum doctor --project "/path/to/project"
```

Do not expose the MCP server as a network service in the alpha release.
