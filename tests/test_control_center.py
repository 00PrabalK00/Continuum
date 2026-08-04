import json
import re
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from continuum import control_center
from continuum.control_center import ControlCenter, ControlCenterServer
from continuum.providers import ProviderManager
from continuum.teams import TeamManager
from continuum.worktrees import WorktreeManager

UI_APP_JS = Path(control_center.__file__).resolve().parent / "ui" / "app.js"


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

            self.assertIn("<title>Continuum</title>", html)
            # Four tabs, and Now is the one that opens: the page leads with where
            # the project stands rather than with orchestration.
            for view in ("now", "history", "notes", "advanced"):
                self.assertIn(f'data-view="{view}"', html)
            self.assertIn('class="tab active" data-view="now"', html)
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


    def _git_project(self, root: Path) -> Path:
        project = root / "project"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Continuum Test"], cwd=project, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=project, capture_output=True, check=True)
        (project / "app.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=project, capture_output=True, check=True)
        return project

    def _serve(self, app: ControlCenter):
        server = ControlCenterServer(("127.0.0.1", 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, f"http://127.0.0.1:{server.server_port}"

    def test_trust_endpoints_on_empty_store_return_200_and_sane_json(self):
        # No db_file, no tasks: every trust endpoint must serve valid JSON, never 500.
        with tempfile.TemporaryDirectory() as temporary:
            app = ControlCenter(Path(temporary) / "project")
            self.assertFalse(app.store.db_file.exists())
            server, thread, url = self._serve(app)
            try:
                with urllib.request.urlopen(url + "/api/flight-records") as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read().decode("utf-8")), [])
                with urllib.request.urlopen(url + "/api/context-packets") as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read().decode("utf-8")), [])
                with urllib.request.urlopen(url + "/api/timeline") as response:
                    self.assertEqual(response.status, 200)
                    timeline = json.loads(response.read().decode("utf-8"))
                self.assertEqual(timeline, {"lanes": [], "blocks": [], "workflows": [], "schedules": []})
                with urllib.request.urlopen(url + "/api/worktree-board") as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read().decode("utf-8")), {"schedules": [], "standalone": []})
                with urllib.request.urlopen(url + "/api/roi") as response:
                    self.assertEqual(response.status, 200)
                    roi = json.loads(response.read().decode("utf-8"))
                self.assertEqual(roi["tasks_total"], 0)
                self.assertEqual(roi["flight_records"], 0)
                self.assertEqual(roi["cost_per_accepted_change_tokens"], 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_flight_record_with_bogus_task_returns_clean_400(self):
        # FlightRecordError / ValueError must be caught and serialized, never a traceback / 500.
        with tempfile.TemporaryDirectory() as temporary:
            app = ControlCenter(Path(temporary) / "project")
            app.store.initialize(1000, 0.8)
            server, thread, url = self._serve(app)
            try:
                for ref in ("BOGUS", "T9999", ""):
                    with self.assertRaises(urllib.error.HTTPError) as context:
                        urllib.request.urlopen(url + f"/api/flight-record?task={ref}")
                    self.assertEqual(context.exception.code, 400)
                    body = json.loads(context.exception.read().decode("utf-8"))
                    self.assertIn("error", body)
                    self.assertTrue(body["error"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_timeline_and_board_with_standalone_worktree_only(self):
        # A standalone worktree (no schedule) must appear on the board and timeline.
        with tempfile.TemporaryDirectory() as temporary:
            project = self._git_project(Path(temporary))
            app = ControlCenter(project)
            app.store.initialize(1000, 0.8)
            task = app.store.create_task("Standalone task", "parallel")
            app.store.claim_files(task["task_id"], "claude", ["app.py"])
            WorktreeManager(app.store).create(task["task_id"])

            board = app.worktree_board()
            self.assertEqual(board["schedules"], [])
            self.assertEqual(board["standalone"][0]["task_id"], task["task_id"])
            timeline = app.timeline()
            self.assertTrue(any(block["id"] == task["task_id"] for block in timeline["blocks"]))

    def test_timeline_and_board_with_scheduled_lanes(self):
        # A worktree schedule yields lanes on the board and lane blocks on the timeline,
        # and those lanes are not double-counted as standalone.
        with tempfile.TemporaryDirectory() as temporary:
            project = self._git_project(Path(temporary))
            (project / "tests").mkdir()
            (project / "tests" / "t.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "tests"], cwd=project, capture_output=True, check=True)
            app = ControlCenter(project)
            app.store.initialize(1000, 0.8)
            manager = WorktreeManager(app.store)
            schedule = manager.schedule(
                "Build feature",
                ["backend:claude:app.py", "qa:codex:tests"],
            )

            board = app.worktree_board()
            self.assertEqual(len(board["schedules"]), 1)
            self.assertEqual(board["schedules"][0]["schedule_id"], schedule["schedule_id"])
            self.assertEqual(board["standalone"], [])
            self.assertGreaterEqual(len(board["schedules"][0]["lanes"]), 2)
            timeline = app.timeline()
            lane_titles = [block["title"] for block in timeline["blocks"]]
            self.assertTrue(any("Build feature" in title for title in lane_titles))

    def test_timeline_and_board_empty_when_nothing_scheduled(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = ControlCenter(Path(temporary) / "project")
            app.store.initialize(1000, 0.8)
            self.assertEqual(app.timeline()["blocks"], [])
            self.assertEqual(app.worktree_board(), {"schedules": [], "standalone": []})

    def test_context_packets_degrade_when_intel_raises(self):
        # If score_intel / gather_context_intel raise, context_packets must degrade
        # gracefully (broad except path) instead of propagating a 500.
        with tempfile.TemporaryDirectory() as temporary:
            app = ControlCenter(Path(temporary) / "project")
            app.store.initialize(1000, 0.8)
            app.store.create_task("Intel task", "sequential")

            def boom(*args, **kwargs):
                raise RuntimeError("intel exploded")

            original_score = control_center.score_intel
            original_gather = control_center.gather_context_intel
            control_center.score_intel = boom
            control_center.gather_context_intel = boom
            try:
                packets = app.context_packets()
            finally:
                control_center.score_intel = original_score
                control_center.gather_context_intel = original_gather

            self.assertEqual(len(packets), 1)
            packet = packets[0]
            self.assertEqual(packet["role"], "coder")
            self.assertIsNone(packet["estimated_tokens"])
            self.assertIsNone(packet["risk_level"])
            self.assertEqual(packet["files"], [])
            self.assertEqual(packet["missing_info"], [])

    def test_trust_endpoints_are_get_readable_without_token(self):
        # Read-only trust endpoints must not require the POST mutation token,
        # while mutations still require it.
        with tempfile.TemporaryDirectory() as temporary:
            app = ControlCenter(Path(temporary) / "project")
            app.store.initialize(1000, 0.8)
            server, thread, url = self._serve(app)
            try:
                for endpoint in (
                    "/api/flight-records", "/api/timeline", "/api/worktree-board",
                    "/api/context-packets", "/api/roi",
                ):
                    # No X-Continuum-Token header at all.
                    with urllib.request.urlopen(url + endpoint) as response:
                        self.assertEqual(response.status, 200)
                # Mutation on the same server is still gated by the token.
                request = urllib.request.Request(
                    url + "/api/teams/create",
                    data=json.dumps({"preset": "local_only"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(request)
                self.assertEqual(context.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_trust_endpoints_preserve_malicious_strings_verbatim_for_ui_escaping(self):
        # The API is a data layer: it must return user-controlled strings verbatim
        # (no HTML mangling). XSS-safety is the UI's job via escapeHtml, asserted
        # separately in test_app_js_escapes_user_controlled_trust_values.
        payload = '<img src=x onerror=alert(1)>'
        with tempfile.TemporaryDirectory() as temporary:
            project = self._git_project(Path(temporary))
            app = ControlCenter(project)
            app.store.initialize(1000, 0.8)
            task = app.store.create_task(payload, "parallel")
            app.store.claim_files(task["task_id"], payload, ["app.py"])
            WorktreeManager(app.store).create(task["task_id"])

            records = app.flight_records()
            self.assertEqual(records[0]["objective"], payload)
            self.assertEqual(records[0]["agent"], payload)
            timeline = app.timeline()
            self.assertTrue(any(block["title"] == payload for block in timeline["blocks"]))

    def test_app_js_escapes_every_recorded_value_it_renders(self):
        """Recorded text is written by agents and by people, so none of it is
        trusted markup.

        This checks the rule rather than a list of call sites, which is what the
        previous version did: it named specific interpolations from the old
        views, so it went green whenever those views were removed rather than
        when escaping was still correct. tests/ui_smoke.js proves the escaping
        works by rendering a hostile string; this proves nothing was left
        unescaped anywhere in the file.
        """
        source = UI_APP_JS.read_text(encoding="utf-8")
        self.assertIn("const esc =", source, "the escaping helper is gone")

        # Every interpolation of a data field, as opposed to a local literal or a
        # helper call, has to pass through esc(). `${esc(...)}`, `${card(...)}`,
        # `${list(...)}` and ternaries are fine; `${item.task}` is not.
        raw = re.findall(r"\$\{\s*([a-z][A-Za-z0-9_]*)\.([A-Za-z0-9_]+)\s*\}", source)
        allowed_objects = {"state", "response", "location", "toast", "event"}
        offenders = [
            f"${{{obj}.{field}}}"
            for obj, field in raw
            if obj not in allowed_objects
        ]
        self.assertEqual(offenders, [], f"Unescaped recorded values: {offenders}")



if __name__ == "__main__":
    unittest.main()
