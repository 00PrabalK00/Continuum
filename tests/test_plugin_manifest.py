"""The Claude Code plugin manifests have to stay true.

A manifest is only read by someone else's tool, so nothing here fails when it
drifts: the version silently disagrees with the package, or the declared MCP
command stops existing, and the first person to notice is a stranger whose
install did nothing.
"""

import json
import unittest
from pathlib import Path

from continuum import __version__
from continuum.cli import parser

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
MCP = ROOT / ".mcp.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ManifestTest(unittest.TestCase):
    def test_both_manifests_are_valid_json(self):
        self.assertIsInstance(load(PLUGIN), dict)
        self.assertIsInstance(load(MARKETPLACE), dict)

    def test_the_plugin_version_matches_the_package(self):
        self.assertEqual(load(PLUGIN)["version"], __version__)

    def test_the_marketplace_entry_matches_the_plugin(self):
        entry = load(MARKETPLACE)["plugins"][0]
        plugin = load(PLUGIN)
        self.assertEqual(entry["name"], plugin["name"])
        self.assertEqual(entry["version"], plugin["version"])

    def test_the_server_is_declared_where_claude_code_reads_it(self):
        # Claude Code reads MCP servers from .mcp.json at the plugin root, not
        # from an mcpServers key in plugin.json. Declaring it in plugin.json
        # installs cleanly and registers nothing, which is what happened: the
        # component inventory reported "MCP servers (0)" while the README said
        # installing registers the server.
        self.assertTrue(MCP.exists(), ".mcp.json is what actually registers the server")
        self.assertNotIn("mcpServers", load(PLUGIN))

    def test_the_declared_mcp_command_exists(self):
        # The manifest promises `continuum mcp serve`. If that subcommand is
        # renamed, the plugin installs and then does nothing.
        server = load(MCP)["continuum"]
        self.assertEqual(server["command"], "continuum")
        self.assertEqual(server["args"][:2], ["mcp", "serve"])

    def test_the_cli_still_has_that_subcommand(self):
        root = parser()
        commands = next(a for a in root._actions if getattr(a, "choices", None) and "mcp" in a.choices)
        mcp = commands.choices["mcp"]
        serve = next(a for a in mcp._actions if getattr(a, "choices", None) and "serve" in a.choices)
        self.assertIn("serve", serve.choices)

    def test_the_project_directory_is_passed_through(self):
        # Without it the server would scope itself to the process working
        # directory, which is not necessarily the project being opened.
        args = load(MCP)["continuum"]["args"]
        self.assertIn("--project", args)
        self.assertTrue(any("CLAUDE_PROJECT_DIR" in item for item in args))


if __name__ == "__main__":
    unittest.main()
