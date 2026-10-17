"""Background work for the window: the same commands the terminal runs, on a thread, with what they print (the
messages of `typer.echo`) sent to the window instead of the console."""

from __future__ import annotations

import contextlib
import io
import threading
import traceback
from collections.abc import Callable
from typing import Final

import typer
from PySide6.QtCore import QObject, Signal


class LineSink(io.TextIOBase):
    """A text stream that hands each finished line to `emit`."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        super().__init__()
        self._emit = emit
        self._pending = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._pending += text
        *lines, self._pending = self._pending.split("\n")
        for line in lines:
            self._emit(line.rstrip("\r"))
        return len(text)

    def flush(self) -> None:
        if self._pending.strip():
            self._emit(self._pending.rstrip("\r"))
        self._pending = ""


_CAPTURE: Final = threading.Lock()
"""Redirecting the output is process-wide: two jobs doing it at once would leave it pointing at the wrong place."""


def run_captured(work: Callable[[], None], emit: Callable[[str], None]) -> str:
    """Run `work` with stdout and stderr going to `emit`. Returns "" when it finished, else why it did not (a command
    that stops with `typer.Exit(code != 0)` has already said why on stderr). Only one such job runs at a time."""
    if not _CAPTURE.acquire(blocking=False):
        return "another job is running: wait for it, or stop it first"
    sink = LineSink(emit)
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            work()
    except typer.Exit as exc:
        return "" if not exc.exit_code else f"stopped (code {exc.exit_code}): see the messages above"
    except Exception as exc:  # a background job must never take the window down: it is reported, with the trace
        emit(traceback.format_exc().rstrip())
        return f"{type(exc).__name__}: {exc}"
    finally:
        sink.flush()
        _CAPTURE.release()
    return ""


class Task(QObject):
    """One command at a time on a thread: `line` for what it prints, `finished` with "" or the reason it failed."""

    line = Signal(str)
    finished = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, work: Callable[[], None]) -> bool:
        """False when one is still running."""
        if self.running:
            return False

        def job() -> None:
            self.finished.emit(run_captured(work, self.line.emit))

        self._thread = threading.Thread(target=job, name="e7-task", daemon=True)
        self._thread.start()
        return True
