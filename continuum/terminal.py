"""Cross-platform interactive terminal execution for agent CLIs."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, TextIO


class TerminalUnavailable(RuntimeError):
    """Raised when this host cannot provide the requested terminal backend."""


OutputCallback = Callable[[str], None]


def terminal_backend() -> str:
    """Return the backend used for real interactive sessions on this platform."""
    if os.name == "nt":
        try:
            from winpty import PtyProcess  # noqa: F401
        except ImportError:
            return "unavailable"
        return "conpty (pywinpty)"
    return "pty"


def run_terminal_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None,
    on_output: OutputCallback,
    input_stream: TextIO | None = None,
    scripted_input: str | None = None,
) -> int:
    """Run ``command`` attached to a real terminal and stream decoded output."""
    if os.name == "nt":
        return _run_windows(command, cwd, env, on_output, input_stream, scripted_input)
    return _run_posix(command, cwd, env, on_output, input_stream, scripted_input)


def _terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size((80, 24))
    return size.lines, size.columns


def _run_windows(
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None,
    on_output: OutputCallback,
    input_stream: TextIO | None,
    scripted_input: str | None,
) -> int:
    try:
        from winpty import PtyProcess
    except ImportError as error:
        raise TerminalUnavailable(
            "Interactive terminal mode on Windows requires pywinpty. "
            "Run `py -m pip install pywinpty`, then retry with `--interactive`."
        ) from error

    rows, cols = _terminal_size()
    process = PtyProcess.spawn(
        command,
        cwd=str(cwd),
        env=env,
        dimensions=(rows, cols),
    )
    stop = threading.Event()

    def read_output() -> None:
        while not stop.is_set():
            try:
                chunk = process.read(4096)
            except EOFError:
                break
            if chunk:
                on_output(chunk)
            elif not process.isalive():
                break

    reader = threading.Thread(target=read_output, name="continuum-terminal-reader", daemon=True)
    reader.start()
    try:
        if scripted_input is not None:
            process.write(scripted_input)
        else:
            _forward_windows_input(process, input_stream or sys.stdin)
        process.wait()
        reader.join(timeout=2.0)
    except KeyboardInterrupt:
        process.sendintr()
        process.wait()
    finally:
        stop.set()
        reader.join(timeout=1.0)
        if process.isalive():
            process.terminate(force=True)
        process.close()
    return int(process.exitstatus or 0)


def _forward_windows_input(process: object, input_stream: TextIO) -> None:
    """Forward keys while checking process liveness and terminal resizes."""
    if not getattr(input_stream, "isatty", lambda: False)():
        for chunk in iter(lambda: input_stream.read(1024), ""):
            if not chunk or not process.isalive():
                return
            process.write(chunk)
        return

    import msvcrt

    special_keys = {
        "H": "\x1b[A",
        "P": "\x1b[B",
        "K": "\x1b[D",
        "M": "\x1b[C",
        "G": "\x1b[H",
        "O": "\x1b[F",
        "S": "\x1b[3~",
        "I": "\x1b[5~",
        "Q": "\x1b[6~",
    }
    previous_size = _terminal_size()
    while process.isalive():
        current_size = _terminal_size()
        if current_size != previous_size:
            process.setwinsize(*current_size)
            previous_size = current_size
        if msvcrt.kbhit():
            value = msvcrt.getwch()
            if value in ("\x00", "\xe0"):
                value = special_keys.get(msvcrt.getwch(), "")
            if value == "\x03":
                process.sendintr()
            elif value:
                process.write(value)
        else:
            time.sleep(0.01)


def _run_posix(
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None,
    on_output: OutputCallback,
    input_stream: TextIO | None,
    scripted_input: str | None,
) -> int:
    import errno
    import fcntl
    import pty
    import select
    import struct
    import termios
    import tty

    master, slave = pty.openpty()

    def resize() -> None:
        rows, cols = _terminal_size()
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    resize()
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave)
    stream = input_stream or sys.stdin
    input_fd = stream.fileno() if scripted_input is None and hasattr(stream, "fileno") else None
    old_mode = None
    prior_winch = None
    exited_at: float | None = None
    try:
        if scripted_input is not None:
            os.write(master, scripted_input.encode())
        elif input_fd is not None and stream.isatty():
            old_mode = termios.tcgetattr(input_fd)
            tty.setraw(input_fd)

        if hasattr(signal, "SIGWINCH"):
            prior_winch = signal.getsignal(signal.SIGWINCH)

            def on_winch(_signum: int, _frame: object) -> None:
                resize()

            signal.signal(signal.SIGWINCH, on_winch)

        while True:
            readers = [master]
            if input_fd is not None and scripted_input is None:
                readers.append(input_fd)
            ready, _, _ = select.select(readers, [], [], 0.05)
            if master in ready:
                try:
                    data = os.read(master, 4096)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    data = b""
                if not data:
                    break
                on_output(data.decode(errors="replace"))
            if input_fd is not None and input_fd in ready:
                value = os.read(input_fd, 1024)
                if not value:
                    break
                os.write(master, value)
            if process.poll() is not None:
                if exited_at is None:
                    exited_at = time.monotonic()
                elif master not in ready and time.monotonic() - exited_at >= 0.2:
                    break
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
    finally:
        if old_mode is not None and input_fd is not None:
            termios.tcsetattr(input_fd, termios.TCSADRAIN, old_mode)
        if prior_winch is not None:
            signal.signal(signal.SIGWINCH, prior_winch)
        os.close(master)
    return process.wait()
