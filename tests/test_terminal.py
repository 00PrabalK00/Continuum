import os
import hashlib
import json
import re
import sys
import time
import tempfile
import threading
import unittest
import warnings
from pathlib import Path

from continuum.terminal import run_terminal_process, terminal_backend


class ReadyInput:
    def __init__(self, ready: threading.Event, payload: str) -> None:
        self.ready = ready
        self.payload = payload
        self.sent = False

    def read(self, _size: int = -1) -> str:
        if self.sent:
            time.sleep(0.05)
            return ""
        if not self.ready.wait(5.0):
            return ""
        self.sent = True
        return self.payload

    def isatty(self) -> bool:
        return False


class TerminalProcessTest(unittest.TestCase):
    def test_backend_name_is_explicit(self):
        expected = {"conpty (pywinpty)", "unavailable"} if os.name == "nt" else {"pty"}
        self.assertIn(terminal_backend(), expected)

    def test_terminal_child_receives_input_and_reports_tty(self):
        if terminal_backend() == "unavailable":
            self.skipTest("pywinpty is installed with the Windows package dependency.")
        chunks = []
        with tempfile.TemporaryDirectory() as temporary:
            with warnings.catch_warnings():
                # pywinpty 3.0.3 emits socket ResourceWarnings on CPython 3.13
                # after a completed ConPTY child despite closing the session.
                warnings.simplefilter("ignore", ResourceWarning)
                returncode = run_terminal_process(
                    [
                        sys.executable,
                        "-c",
                        "import sys; print('tty=' + str(sys.stdin.isatty())); print('reply=' + input())",
                    ],
                    cwd=Path(temporary),
                    env=os.environ.copy(),
                    on_output=chunks.append,
                    scripted_input="hello terminal\r\n" if os.name == "nt" else "hello terminal\n",
                )
        output = "".join(chunks)
        self.assertEqual(returncode, 0)
        self.assertIn("tty=True", output)
        self.assertIn("reply=hello terminal", output)

    def test_terminal_input_receipt_proves_same_live_process_consumed_prompt(self):
        if terminal_backend() == "unavailable":
            self.skipTest("pywinpty is installed with the Windows package dependency.")
        target_id = "target-continuum-pty-receipt"
        user_input = "continue-auth-flow"
        input_hash = hashlib.sha256(user_input.encode("utf-8")).hexdigest()
        script = r"""
import hashlib
import json
import os
import sys
target = os.environ["CONTINUUM_TEST_TARGET_ID"]
expected_hash = os.environ["CONTINUUM_TEST_INPUT_HASH"]
generation = str(os.getpid())
cwd = os.getcwd()
prompt = f"READY:{target}:{generation}"
print(json.dumps({
    "event": "ready",
    "target_id": target,
    "cwd": cwd,
    "process_generation": generation,
    "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
}), flush=True)
line = sys.stdin.readline().rstrip("\r\n")
observed_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
accepted = observed_hash == expected_hash
print(json.dumps({
    "event": "receipt",
    "target_id": target,
    "cwd": cwd,
    "process_generation": generation,
    "input_hash": observed_hash,
    "accepted_by_pty": accepted,
    "transcript_user_record_observed": accepted,
    "status": "accepted" if accepted else "rejected",
}), flush=True)
print(json.dumps({
    "event": "advanced",
    "target_id": target,
    "process_generation": generation,
    "assistant_output_advanced": accepted,
}), flush=True)
sys.exit(0 if accepted else 23)
"""
        chunks = []
        ready_seen = threading.Event()
        with tempfile.TemporaryDirectory() as temporary:
            env = os.environ.copy()
            env["CONTINUUM_TEST_TARGET_ID"] = target_id
            env["CONTINUUM_TEST_INPUT_HASH"] = input_hash
            payload = (user_input + "\r\n") if os.name == "nt" else (user_input + "\n")

            def capture(chunk: str) -> None:
                chunks.append(chunk)
                if '"event": "ready"' in chunk:
                    ready_seen.set()

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ResourceWarning)
                returncode = run_terminal_process(
                    [sys.executable, "-c", script],
                    cwd=Path(temporary),
                    env=env,
                    on_output=capture,
                    input_stream=ReadyInput(ready_seen, payload),
                )
        transcript = "".join(chunks)
        events = []
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", transcript):
            try:
                event, _end = decoder.raw_decode(transcript[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and "event" in event:
                events.append(event)
        ready = next(event for event in events if event["event"] == "ready")
        receipt = next(event for event in events if event["event"] == "receipt")
        advanced = next(event for event in events if event["event"] == "advanced")

        self.assertEqual(returncode, 0, transcript)
        self.assertEqual(receipt["target_id"], ready["target_id"])
        self.assertEqual(receipt["cwd"], ready["cwd"])
        self.assertEqual(receipt["process_generation"], ready["process_generation"])
        self.assertEqual(receipt["cwd"], str(Path(temporary)))
        self.assertEqual(receipt["input_hash"], input_hash)
        self.assertTrue(receipt["accepted_by_pty"])
        self.assertTrue(receipt["transcript_user_record_observed"])
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(advanced["process_generation"], ready["process_generation"])
        self.assertTrue(advanced["assistant_output_advanced"])


if __name__ == "__main__":
    unittest.main()
