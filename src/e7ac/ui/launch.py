"""The window's entry point for a start with no console (SPEC D86): `pythonw -m e7ac.ui`, which is what `Orbis.cmd` and
the desktop shortcut run.

A program with no console has nowhere to print and nobody to tell when it fails to start, so: whatever it prints goes to
`<home>/logs/console.log`, and a failure to start is written to `app.log` and shown in a message box. Qt is imported
only inside `main`, after the streams are in place."""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from typing import Final

from e7ac import crashlog
from e7ac.paths import AppPaths, default_paths

CONSOLE_LOG: Final = "console.log"
CONSOLE_LOG_MAX: Final = 1_000_000
TITLE: Final = "Orbis Codex"
SHOWN_LINES: Final = 8


def main() -> int:
    paths = default_paths()
    own_streams(paths)
    try:
        from e7ac.ui.app import run

        return run()
    except Exception:
        failed(paths, traceback.format_exc())
        return 1


def own_streams(paths: AppPaths) -> None:
    """With no console `sys.stdout` and `sys.stderr` are None, and anything that prints would fail: they become a log
    file (emptied when it grows past `CONSOLE_LOG_MAX`)."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        path = paths.logs_dir / CONSOLE_LOG
        mode = "w" if path.is_file() and path.stat().st_size > CONSOLE_LOG_MAX else "a"
        stream = path.open(mode, encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        return  # nowhere to write either: printing stays impossible, and nothing in the program depends on it
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def failed(paths: AppPaths, trace: str) -> None:
    """The window could not start: say so where it will be seen, and keep the details."""
    log = crashlog.log_path(paths.logs_dir)
    try:
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} CRITICAL the window could not start\n{trace}\n")
    except OSError:
        pass
    tail = "\n".join(trace.strip().splitlines()[-SHOWN_LINES:])
    show_error(
        TITLE,
        f"The window could not start.\n\n{tail}\n\nIf the program was just updated, run Orbis.cmd once: it brings the "
        f"installation up to date.\n\nDetails: {log}",
    )


def show_error(title: str, message: str) -> None:
    """A message box on Windows (there is no console to print to); the error stream elsewhere."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # type: ignore[attr-defined,unused-ignore]
    elif sys.stderr is not None:
        print(f"{title}: {message}", file=sys.stderr)
