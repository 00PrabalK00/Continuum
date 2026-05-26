import io
import json
import tempfile
import unittest
from pathlib import Path

from continuum.core import MemoryStore
from continuum.mcp_server import handle_request, serve_stdio


class McpServerTest(unittest.TestCase):
    def test_tools_list_and_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)

            listed = handle_request(store, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            written = handle_request(
                store,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "write_handoff",
                        "arguments": {"task": "implement mcp", "next_step": "run tests"},
                    },
                },
            )

            names = [tool["name"] for tool in listed["result"]["tools"]]
            self.assertIn("get_latest_handoff", names)
            self.assertIn("get_startup_context", names)
            self.assertIn("expand_memory", names)
            self.assertIn("claim_task_files", names)
            self.assertIn("get_context_packet", names)
            self.assertIn("post_agent_message", names)
            self.assertIn("Handoff written", written["result"]["content"][0]["text"])

    def test_mcp_task_claim_and_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            task = store.create_task("Patch auth")
            claimed = handle_request(
                store,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "claim_task_files",
                        "arguments": {"task_id": task["task_id"], "agent": "codex", "files": ["auth.py"]},
                    },
                },
            )
            done = handle_request(
                store,
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "complete_task",
                        "arguments": {"task_id": task["task_id"], "summary": "Fixed."},
                    },
                },
            )
            self.assertIn("RUNNING", claimed["result"]["content"][0]["text"])
            self.assertIn("DONE", done["result"]["content"][0]["text"])

    def test_mcp_rejects_model_provider_file_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            task = store.create_task("Reason only")
            rejected = handle_request(
                store,
                {
                    "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                    "params": {"name": "claim_task_files", "arguments": {
                        "task_id": task["task_id"], "agent": "openrouter", "files": ["auth.py"]
                    }},
                },
            )

            self.assertIn("Model provider cannot claim", rejected["error"]["message"])

    def test_mcp_exposes_bounded_agent_messages(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            posted = handle_request(
                store,
                {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
                    "name": "post_agent_message", "arguments": {
                        "sender": "explorer", "recipient": "coder", "body": "inspect auth"
                    }
                }},
            )
            read = handle_request(
                store,
                {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
                    "name": "get_agent_messages", "arguments": {"recipient": "coder"}
                }},
            )

            self.assertIn("recorded", posted["result"]["content"][0]["text"])
            self.assertIn("inspect auth", read["result"]["content"][0]["text"])

    def test_stdio_initialize_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            source = io.StringIO(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-03-26"},
                    }
                )
                + "\n"
            )
            result = io.StringIO()

            serve_stdio(store, source, result)

            response = json.loads(result.getvalue())
            self.assertEqual(response["result"]["serverInfo"]["name"], "continuum")


if __name__ == "__main__":
    unittest.main()
