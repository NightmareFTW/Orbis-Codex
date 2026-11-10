"""The window's entry point for a start with no console (SPEC D86): `pythonw -m e7ac.ui`, which is what `Orbis.cmd` and
the desktop shortcut run.

A program with no console has nowhere to print and nobody to tell when it fails to start, so: whatever it prints goes to
`<home>/logs/console.log`, and a failure to start is written to `app.log` and shown in a message box. Qt is imported
only inside `main`, after the streams are in place."""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from typing import Final

from e7ac import crashlog, shortcut
from e7ac.paths import AppPaths, default_paths

CONSOLE_LOG: Final = "console.log"
CONSOLE_LOG_MAX: Final = 1_000_000
TITLE: Final = "Orbis Codex"
SHOWN_LINES: Final = 8


def main() -> int:
    paths = default_paths()
    own_streams(paths)
    release_console(paths)
    try:
        from e7ac.ui.app import run

        return run()
    except Exception:
        failed(paths, traceback.format_exc())
        return 1


def release_console(paths: AppPaths, argv: list[str] | None = None) -> str:
    """Let go of the console this process was given (SPEC D118: a cmd window stayed open while the program ran).

    The window needs no console. If Windows attached one anyway (a launcher that opened `python.exe` instead of
    `pythonw.exe`), the process leaves it: the console closes with the last process that holds it, so a launcher that
    has already ended takes the window with it. What it found goes to `app.log` (which processes shared the console,
    who started this one), to see what keeps a console open when it does not close. `--console` keeps it, for a start
    from a terminal on purpose. Returns the line written ("" when there was nothing to do)."""
    if not shortcut.on_windows() or "--console" in (sys.argv[1:] if argv is None else argv):
        return ""
    import ctypes

    kernel = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
    if not kernel.GetConsoleWindow():
        line = "start: no console"
    else:
        slots = (ctypes.c_ulong * 16)()
        count = int(kernel.GetConsoleProcessList(slots, 16))
        shared = [int(slots[i]) for i in range(min(count, 16))]
        kernel.FreeConsole()
        line = f"start: a console was attached (shared with process ids {shared}, started by {os.getppid()}): left it"
    try:
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        with crashlog.log_path(paths.logs_dir).open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} INFO {line} ({sys.executable})\n")
    except OSError:
        pass
    return line


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
