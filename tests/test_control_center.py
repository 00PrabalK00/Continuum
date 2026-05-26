import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from continuum.control_center import ControlCenter, ControlCenterServer
from continuum.providers import ProviderManager
from continuum.teams import TeamManager


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
                with urllib.request.urlopen(url + "/logo.svg") as response:
                    logo_type = response.headers.get_content_type()
                    logo = response.read().decode("utf-8")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            self.assertIn("Continuum Control Center", html)
            self.assertIn('class="active" data-view="teams"', html)
            self.assertIn('src="/logo.svg"', html)
            self.assertEqual(logo_type, "image/svg+xml")
            self.assertIn("<svg", logo)
            self.assertEqual(payload["project"]["name"], "project")

    def test_http_server_allows_explicit_team_creation_and_workflow_planning(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = ControlCenter(Path(temporary) / "project")
            app.store.initialize(1000, 0.8)
            server = ControlCenterServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                create = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/teams/create",
                    data=json.dumps({"preset": "local_only"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(create) as response:
                    created = json.loads(response.read().decode("utf-8"))
                plan = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/workflows/run",
                    data=json.dumps({"team": "local_only", "request": "document auth"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(plan) as response:
                    workflow = json.loads(response.read().decode("utf-8"))
                self.assertEqual(created["team"], "local_only")
                self.assertEqual(workflow["status"], "PLANNED")
                resume = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/resume-context",
                    data=json.dumps({"role": "reasoner", "mode": "compact"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
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
                data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
            )
            try:
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(request)
                self.assertEqual(context.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
