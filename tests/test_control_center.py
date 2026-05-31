import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from continuum.control_center import ControlCenter, ControlCenterServer
from continuum.providers import ProviderManager
from continuum.teams import TeamManager
from continuum.worktrees import WorktreeManager


class ControlCenterTest(unittest.TestCase):
    def test_overview_reads_configured_state_without_planning_tasks(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = ControlCenter(Path(temporary) / "project")
            app.store.initialize(1000, 0.8)
            ProviderManager(app.store.state_dir).ensure_config()
            TeamManager(app.store).init("default_dev_team")
            (app.store.state_dir / "current.md").write_text("current only", encoding="utf-8")
            (app.store.state_dir / "latest_handoff.md").write_text("handoff only", encoding="utf-8")

            overview = app.overview()

            self.assertEqual(overview["project"]["name"], "project")
            self.assertEqual(overview["current_team"], "default_dev_team")
            self.assertIn("handoff only", overview["latest_handoff"])
            self.assertNotIn("current only", overview["latest_handoff"])
            self.assertEqual(app.tasks(), [])

    def test_opening_ui_does_not_initialize_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            app = ControlCenter(project)

            app.overview()

            self.assertFalse((project / ".continuum" / "config.json").exists())

    def test_http_server_serves_ui_and_json_endpoints(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = ControlCenter(Path(temporary) / "project")
            server = ControlCenterServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(url + "/") as response:
                    html = response.read().decode("utf-8")
                with urllib.request.urlopen(url + "/api/overview") as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urllib.request.urlopen(url + "/logo.png") as response:
                    logo_type = response.headers.get_content_type()
                    logo = response.read()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            self.assertIn("Continuum Control Center", html)
            self.assertIn('class="active" data-view="teams"', html)
            self.assertIn('src="/logo.png"', html)
            self.assertEqual(logo_type, "image/png")
            self.assertEqual(logo[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(logo[25], 6)  # PNG color type 6 is RGBA.
            self.assertEqual(payload["project"]["name"], "project")

    def test_trust_layer_endpoints_expose_timeline_board_context_and_flight(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "Continuum Test"], cwd=project, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=project, capture_output=True, check=True)
            (project / "app.py").write_text("value = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=project, capture_output=True, check=True)
            app = ControlCenter(project)
            app.store.initialize(1000, 0.8)
            task = app.store.create_task("Flight task", "parallel")
            app.store.claim_files(task["task_id"], "claude", ["app.py"])
            manager = WorktreeManager(app.store)
            record = manager.create(task["task_id"])
            (Path(record["path"]) / "app.py").write_text("value = 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=record["path"], capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "change"], cwd=record["path"], capture_output=True, check=True)
            manager.record_tests(task["task_id"], True, "python -m unittest")
            manager.record_review(task["task_id"], True, "approved")

            server = ControlCenterServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(url + "/api/flight-records") as response:
                    flights = json.loads(response.read().decode("utf-8"))
                with urllib.request.urlopen(url + f"/api/flight-record?task={task['task_id']}") as response:
                    flight = json.loads(response.read().decode("utf-8"))
                with urllib.request.urlopen(url + "/api/timeline") as response:
                    timeline = json.loads(response.read().decode("utf-8"))
                with urllib.request.urlopen(url + "/api/worktree-board") as response:
                    board = json.loads(response.read().decode("utf-8"))
                with urllib.request.urlopen(url + "/api/context-packets") as response:
                    packets = json.loads(response.read().decode("utf-8"))
                with urllib.request.urlopen(url + "/api/roi") as response:
                    roi = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            self.assertEqual(flights[0]["task_id"], task["task_id"])
            self.assertEqual(flight["final_status"], "merge_ready")
            self.assertTrue(timeline["blocks"])
            self.assertEqual(board["standalone"][0]["task_id"], task["task_id"])
            self.assertEqual(packets[0]["task_id"], task["task_id"])
            self.assertEqual(roi["flight_records"], 1)

    def test_http_server_allows_explicit_team_creation_and_workflow_planning(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = ControlCenter(Path(temporary) / "project")
            app.store.initialize(1000, 0.8)
            server = ControlCenterServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            auth = {"Content-Type": "application/json", "X-Continuum-Token": server.token}
            try:
                create = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/teams/create",
                    data=json.dumps({"preset": "local_only"}).encode("utf-8"),
                    headers=auth, method="POST",
                )
                with urllib.request.urlopen(create) as response:
                    created = json.loads(response.read().decode("utf-8"))
                plan = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/workflows/run",
                    data=json.dumps({"team": "local_only", "request": "document auth"}).encode("utf-8"),
                    headers=auth, method="POST",
                )
                with urllib.request.urlopen(plan) as response:
                    workflow = json.loads(response.read().decode("utf-8"))
                self.assertEqual(created["team"], "local_only")
                self.assertEqual(workflow["status"], "PLANNED")
                resume = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/resume-context",
                    data=json.dumps({"role": "reasoner", "mode": "compact"}).encode("utf-8"),
                    headers=auth, method="POST",
                )
                with urllib.request.urlopen(resume) as response:
                    packet = json.loads(response.read().decode("utf-8"))
                self.assertIn("Continuum Context Packet", packet["text"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_unknown_action_endpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = ControlCenter(Path(temporary) / "project")
            server = ControlCenterServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/unknown",
                data=b"{}",
                headers={"Content-Type": "application/json", "X-Continuum-Token": server.token},
                method="POST",
            )
            try:
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(request)
                self.assertEqual(context.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_team_save_endpoint_rejects_path_traversal_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = ControlCenter(Path(temporary) / "project")
            app.store.initialize(1000, 0.8)
            server = ControlCenterServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/teams/save",
                data=json.dumps({"name": "../outside", "config": {"agents": {}, "routing": {}}}).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Continuum-Token": server.token},
                method="POST",
            )
            try:
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(request)
                self.assertEqual(context.exception.code, 400)
                self.assertFalse((app.store.state_dir / "outside.json").exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


    def test_post_without_token_is_rejected_with_403(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = ControlCenter(Path(temporary) / "project")
            app.store.initialize(1000, 0.8)
            server = ControlCenterServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/teams/create",
                data=json.dumps({"preset": "local_only"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            wrong = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/teams/create",
                data=json.dumps({"preset": "local_only"}).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Continuum-Token": "wrong"}, method="POST",
            )
            try:
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(request)
                self.assertEqual(context.exception.code, 403)
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(wrong)
                self.assertEqual(context.exception.code, 403)
                self.assertFalse((app.store.state_dir / "teams").exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_post_with_valid_token_succeeds_and_token_is_served_in_html(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = ControlCenter(Path(temporary) / "project")
            app.store.initialize(1000, 0.8)
            server = ControlCenterServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(url + "/") as response:
                    html = response.read().decode("utf-8")
                self.assertIn(f'<meta name="continuum-token" content="{server.token}">', html)
                create = urllib.request.Request(
                    url + "/api/teams/create",
                    data=json.dumps({"preset": "local_only"}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Continuum-Token": server.token},
                    method="POST",
                )
                with urllib.request.urlopen(create) as response:
                    created = json.loads(response.read().decode("utf-8"))
                self.assertEqual(created["team"], "local_only")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
