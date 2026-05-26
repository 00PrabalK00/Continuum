# Security

Continuum stores agent output, local change events and handoff notes on the
local machine. That content can include secrets printed during development.

- Do not commit `.continuum/` or mirrored private Obsidian notes.
- Review memory output before sharing a repository archive or screen capture.
- Treat MCP `write_handoff` as a local write-capable tool.
- Provide API keys through environment variables; Continuum does not write keys to events or notes.
- Control Center binds to `127.0.0.1` by default and is read-only.
- MCP is provided over stdio only in this release.
- Report security issues privately to the maintainers before public disclosure.
