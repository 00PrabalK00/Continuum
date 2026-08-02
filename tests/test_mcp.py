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
            self.assertIn("get_external_sessions", names)
            self.assertIn("Recorded: implement mcp", written["result"]["content"][0]["text"])

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

    def test_get_startup_context_returns_compact_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            store.event("decision", {"summary": "use pytest"})
            result = handle_request(store, {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "get_startup_context", "arguments": {}}})
            self.assertIn("text", result["result"]["content"][0])
            self.assertGreater(len(result["result"]["content"][0]["text"]), 0)

    def test_get_current_state_returns_state_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            result = handle_request(store, {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "get_current_state", "arguments": {}}})
            self.assertIn("text", result["result"]["content"][0])

    def test_search_memory_returns_matching_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            store.event("decision", {"summary": "authentication retry logic"})
            store.event("decision", {"summary": "ui color scheme"})
            result = handle_request(store, {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "search_memory", "arguments": {"query": "authentication"}}})
            text = result["result"]["content"][0]["text"]
            self.assertIn("authentication", text.lower())

    def test_search_memory_returns_no_match_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            result = handle_request(store, {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "search_memory", "arguments": {"query": "nonexistent"}}})
            self.assertIn("No matching", result["result"]["content"][0]["text"])

    def test_expand_memory_returns_event_detail(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            store.event("decision", {"summary": "use async"})
            events = store.recent_events(10)
            decision_id = next(e["id"] for e in events if e["kind"] == "decision")
            result = handle_request(store, {"jsonrpc": "2.0", "id": 14, "method": "tools/call", "params": {"name": "expand_memory", "arguments": {"memory_id": decision_id}}})
            self.assertIn("use async", result["result"]["content"][0]["text"])

    def test_expand_memory_returns_not_found(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            result = handle_request(store, {"jsonrpc": "2.0", "id": 15, "method": "tools/call", "params": {"name": "expand_memory", "arguments": {"memory_id": 9999}}})
            self.assertIn("not found", result["result"]["content"][0]["text"])

    def test_get_raw_log_returns_log_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            log_dir = store.state_dir / "session_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "test.log").write_text("line1\nline2\n", encoding="utf-8")
            result = handle_request(store, {"jsonrpc": "2.0", "id": 16, "method": "tools/call", "params": {"name": "get_raw_log", "arguments": {"filename": "test.log"}}})
            self.assertIn("line1", result["result"]["content"][0]["text"])

    def test_get_raw_log_returns_not_found(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            result = handle_request(store, {"jsonrpc": "2.0", "id": 17, "method": "tools/call", "params": {"name": "get_raw_log", "arguments": {"filename": "missing.log"}}})
            self.assertIn("not found", result["result"]["content"][0]["text"])

    def test_get_open_tasks_lists_non_final_tasks(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            store.create_task("Fix auth")
            result = handle_request(store, {"jsonrpc": "2.0", "id": 18, "method": "tools/call", "params": {"name": "get_open_tasks", "arguments": {}}})
            self.assertIn("T0001", result["result"]["content"][0]["text"])

    def test_get_open_tasks_returns_no_tasks_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            result = handle_request(store, {"jsonrpc": "2.0", "id": 19, "method": "tools/call", "params": {"name": "get_open_tasks", "arguments": {}}})
            self.assertIn("No open tasks", result["result"]["content"][0]["text"])

    def test_get_context_packet_returns_bounded_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            store.write_handoff("fix auth", "run tests")
            result = handle_request(store, {"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": {"name": "get_context_packet", "arguments": {"role": "coder", "mode": "compact"}}})
            self.assertIn("Estimated context:", result["result"]["content"][0]["text"])

    def test_get_workflows_returns_empty_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            result = handle_request(store, {"jsonrpc": "2.0", "id": 21, "method": "tools/call", "params": {"name": "get_workflows", "arguments": {}}})
            self.assertIn("No workflows", result["result"]["content"][0]["text"])

    def test_unknown_tool_returns_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            result = handle_request(store, {"jsonrpc": "2.0", "id": 22, "method": "tools/call", "params": {"name": "nonexistent_tool", "arguments": {}}})
            self.assertIn("error", result)
            self.assertIn("Unknown tool", result["error"]["message"])

    def test_unknown_method_returns_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            result = handle_request(store, {"jsonrpc": "2.0", "id": 23, "method": "invalid/method", "params": {}})
            self.assertIn("error", result)
            self.assertIn("Method not found", result["error"]["message"])

    def test_no_id_returns_none(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            result = handle_request(store, {"jsonrpc": "2.0", "method": "tools/list"})
            self.assertIsNone(result)

    def test_mcp_reads_attached_external_session_context_packet(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "project")
            store.initialize(1000, 0.8)
            session = store.register_external_session(20, 1.0, "gemini", str(store.project), "gemini")
            store.publish_external_session_context(session["session_id"], "compact")

            listed = handle_request(
                store,
                {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "get_external_sessions", "arguments": {}}},
            )
            packet = handle_request(
                store,
                {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {
                    "name": "get_external_session_context", "arguments": {"session_id": "S0001"}
                }},
            )

            self.assertIn("S0001 ATTACHED gemini", listed["result"]["content"][0]["text"])
            self.assertIn("External Session Context", packet["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
