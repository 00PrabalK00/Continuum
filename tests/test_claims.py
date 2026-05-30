import tempfile
import unittest
from pathlib import Path

from continuum.core import MemoryStore


def make_store(temporary: str) -> MemoryStore:
    store = MemoryStore(Path(temporary) / "project")
    store.initialize(1000, 0.8)
    return store


class ClaimRecoveryTest(unittest.TestCase):
    def test_list_claims_reports_file_task_agent_and_age(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(temporary)
            task = store.create_task("implement feature")
            store.claim_files(task["task_id"], "claude", ["src/app.py", "src/util.py"])

            claims = store.list_claims()

            self.assertEqual(len(claims), 2)
            paths = {claim["path"] for claim in claims}
            self.assertEqual(paths, {"src/app.py", "src/util.py"})
            for claim in claims:
                self.assertEqual(claim["task_id"], task["task_id"])
                self.assertEqual(claim["agent"], "claude")
                self.assertEqual(claim["task_status"], "RUNNING")
                self.assertTrue(claim["since"])

    def test_release_records_audit_reason_and_frees_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(temporary)
            task = store.create_task("implement feature")
            store.claim_files(task["task_id"], "claude", ["src/app.py"])
            store.set_task_status(task["task_id"], "BLOCKED")

            result = store.release_claim(task["task_id"], "agent died mid-task")

            self.assertEqual(result["released"], ["src/app.py"])
            self.assertEqual(store.list_claims(), [])
            released_events = [event for event in store.recent_events(50) if event["kind"] == "claim_released"]
            self.assertEqual(len(released_events), 1)
            self.assertEqual(released_events[0]["payload"]["reason"], "agent died mid-task")
            self.assertEqual(released_events[0]["payload"]["files"], ["src/app.py"])

    def test_release_single_file_leaves_other_claims(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(temporary)
            task = store.create_task("implement feature")
            store.claim_files(task["task_id"], "claude", ["src/app.py", "src/util.py"])
            store.set_task_status(task["task_id"], "BLOCKED")

            result = store.release_claim(task["task_id"], "rescope", file="src/app.py")

            self.assertEqual(result["released"], ["src/app.py"])
            remaining = {claim["path"] for claim in store.list_claims()}
            self.assertEqual(remaining, {"src/util.py"})

    def test_release_requires_a_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(temporary)
            task = store.create_task("implement feature")
            store.claim_files(task["task_id"], "claude", ["src/app.py"])
            store.set_task_status(task["task_id"], "BLOCKED")

            with self.assertRaisesRegex(ValueError, "reason"):
                store.release_claim(task["task_id"], "   ")

    def test_running_task_claim_not_released_without_force(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(temporary)
            task = store.create_task("implement feature")
            store.claim_files(task["task_id"], "claude", ["src/app.py"])

            self.assertEqual(store.get_task(task["task_id"])["status"], "RUNNING")
            with self.assertRaisesRegex(ValueError, "RUNNING"):
                store.release_claim(task["task_id"], "trying to release a live task")
            self.assertEqual(len(store.list_claims()), 1)

            result = store.release_claim(task["task_id"], "agent confirmed dead", force=True)
            self.assertEqual(result["released"], ["src/app.py"])
            forced_event = [event for event in store.recent_events(50) if event["kind"] == "claim_released"][0]
            self.assertTrue(forced_event["payload"]["forced"])

    def test_recover_stale_only_targets_terminal_task_orphans(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(temporary)
            live = store.create_task("live work")
            store.claim_files(live["task_id"], "claude", ["src/live.py"])
            orphan = store.create_task("crashed work")
            store.claim_files(orphan["task_id"], "claude", ["src/orphan.py"])
            # Simulate a crash that left the lock behind despite a terminal status by
            # re-inserting the lock after the task is forced FAILED.
            connection = store.connect()
            connection.execute(
                "UPDATE tasks SET status = ? WHERE id = ?",
                ("FAILED", store.parse_task_ref(orphan["task_id"])),
            )
            connection.execute(
                "INSERT OR REPLACE INTO file_locks(path, task_id, agent, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                ("src/orphan.py", store.parse_task_ref(orphan["task_id"]), "claude", "2026-01-01T00:00:00+00:00", None),
            )
            connection.commit()
            connection.close()

            recovered = store.recover_stale_claims("automated stale sweep")

            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["task_id"], orphan["task_id"])
            self.assertEqual(recovered[0]["task_status"], "FAILED")
            self.assertEqual(recovered[0]["released"], ["src/orphan.py"])
            # The live (RUNNING) task's claim is untouched.
            remaining = {claim["path"] for claim in store.list_claims()}
            self.assertEqual(remaining, {"src/live.py"})
            recovered_events = [event for event in store.recent_events(50) if event["kind"] == "claim_recovered"]
            self.assertEqual(len(recovered_events), 1)

    def test_recover_stale_with_no_orphans_is_noop(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(temporary)
            task = store.create_task("live work")
            store.claim_files(task["task_id"], "claude", ["src/live.py"])

            recovered = store.recover_stale_claims("sweep")

            self.assertEqual(recovered, [])
            self.assertEqual(len(store.list_claims()), 1)


if __name__ == "__main__":
    unittest.main()
