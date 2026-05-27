import os
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

from continuum.terminal import run_terminal_process, terminal_backend


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


if __name__ == "__main__":
    unittest.main()
