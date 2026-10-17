"""What to look at when the window closes by itself (SPEC D85).

A native crash (the OCR engine, the screen capture, Tk or Qt) ends the process without a Python traceback and, on
Windows, without a message the user can read. Three things are kept instead, all under `<home>/logs/`:

- `crash-<pid>.log`: `faulthandler` dumps the Python stack of every thread there when the process dies on a fatal error;
  an empty file is removed on a clean exit, a non-empty one of a dead process is archived as `crash-<time>.log`;
- `app.log`: the errors that did not kill the program (uncaught exceptions of any thread, Qt warnings), with a size cap;
- `running-<pid>.json`: a marker that exists only while the program runs; one left by a process that is gone means that
  run did not end normally (crash, console closed, PC restarted). `install` reports it as a `PreviousRun`.

Nothing here leaves the PC and nothing touches the game.
"""

from __future__ import annotations

import atexit
import contextlib
import faulthandler
import json
import logging
import os
import platform
import sys
import threading
import types
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, Any, Final

from e7ac import __version__

LOG_NAME: Final = "app.log"
LOG_MAX_BYTES: Final = 400_000
KEEP_CRASH_LOGS: Final = 5
_MARKER: Final = "running-{pid}.json"
_DUMP: Final = "crash-{pid}.log"
_ARCHIVE_FORMAT: Final = "crash-%Y%m%d-%H%M%S.log"
LOGGER: Final = logging.getLogger("e7ac")


@dataclass(frozen=True, slots=True)
class PreviousRun:
    """A run that did not end normally."""

    started: datetime | None
    """When it started (UTC), when its marker could be read."""
    dump: Path | None
    """The archived `faulthandler` dump it left (the process died on a fatal error), if any."""

    def describe(self) -> str:
        """The notice for the window, in words."""
        when = f" at {self.started:%Y-%m-%d %H:%M} UTC" if self.started is not None else ""
        report = (
            f" It left a fatal-error report ({self.dump.name})."
            if self.dump is not None
            else " It left no fatal-error report: the console may have been closed, or the PC restarted."
        )
        return f"The previous run, started{when}, did not end normally.{report}"


def log_path(logs_dir: Path) -> Path:
    return logs_dir / LOG_NAME


def alive(pid: int) -> bool:
    """Whether a process with this id is running (a reused id counts as running: a missed notice, never a false one)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # PermissionError: it exists, it is somebody else's
    return True


class _Session:
    """What `install` set up, so that `shutdown` can undo it."""

    def __init__(self, logs_dir: Path) -> None:
        self.logs_dir = logs_dir
        self.pid = os.getpid()
        self.marker = logs_dir / _MARKER.format(pid=self.pid)
        self.dump_path = logs_dir / _DUMP.format(pid=self.pid)
        self.dump: IO[str] | None = None
        self.handler: RotatingFileHandler | None = None
        self.hooks: tuple[Any, Any, Any] = (None, None, None)
        self.faulthandler_was_on = False


_session: _Session | None = None


def install(logs_dir: Path) -> PreviousRun | None:
    """Start watching this run. Returns what the previous run left when it did not end normally (None otherwise).
    Idempotent per process: a second call is a no-op returning None."""
    global _session
    if _session is not None:
        return None
    logs_dir.mkdir(parents=True, exist_ok=True)
    previous = _previous_run(logs_dir)
    session = _Session(logs_dir)
    session.faulthandler_was_on = faulthandler.is_enabled()
    try:
        session.dump = session.dump_path.open("w", encoding="utf-8")
        faulthandler.enable(file=session.dump, all_threads=True)
    except (OSError, RuntimeError, ValueError):
        session.dump = None  # no dump file: the log and the marker still work
    handler = RotatingFileHandler(log_path(logs_dir), maxBytes=LOG_MAX_BYTES, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    session.handler = handler
    session.hooks = (sys.excepthook, threading.excepthook, sys.unraisablehook)
    sys.excepthook = _on_exception
    threading.excepthook = _on_thread_exception
    sys.unraisablehook = _on_unraisable
    _write_marker(session)
    _session = session
    LOGGER.info(
        "session start pid=%s version=%s python=%s os=%s",
        session.pid,
        __version__,
        platform.python_version(),
        platform.platform(),
    )
    if previous is not None:
        LOGGER.warning(
            "the previous run did not end normally (started %s)%s",
            previous.started.isoformat(timespec="seconds") if previous.started else "?",
            f"; fatal-error dump: {previous.dump.name}" if previous.dump else "",
        )
    atexit.register(shutdown)
    return previous


def shutdown() -> None:
    """This run ended normally: remove the marker, close the dump (an empty one is deleted), restore the hooks."""
    global _session
    session, _session = _session, None
    if session is None:
        return
    LOGGER.info("session end pid=%s", session.pid)
    sys.excepthook, threading.excepthook, sys.unraisablehook = session.hooks
    faulthandler.disable()
    if session.faulthandler_was_on:
        faulthandler.enable()
    if session.dump is not None:
        session.dump.close()
        _remove_if_empty(session.dump_path)
    if session.handler is not None:
        LOGGER.removeHandler(session.handler)
        session.handler.close()
    session.marker.unlink(missing_ok=True)
    atexit.unregister(shutdown)


def note(message: str, level: int = logging.WARNING) -> None:
    """Put a line in `app.log` (a no-op before `install`: the logger has no handler then)."""
    LOGGER.log(level, "%s", message)


def recent_lines(logs_dir: Path, limit: int = 40) -> list[str]:
    """The last `limit` lines of `app.log` that are warnings or worse, with the traceback lines that follow them."""
    path = log_path(logs_dir)
    if not path.is_file():
        return []
    keep: list[str] = []
    in_problem = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line[:4].isdigit() and line[4:5] == "-":  # a new record: "2026-10-18 12:00:00,123 LEVEL ..."
            in_problem = " WARNING " in line[:40] or " ERROR " in line[:40] or " CRITICAL " in line[:40]
        if in_problem:
            keep.append(line)
    return keep[-limit:]


def latest_dump(logs_dir: Path) -> Path | None:
    """The newest archived fatal-error dump."""
    dumps = sorted(logs_dir.glob("crash-????????-??????.log"))
    return dumps[-1] if dumps else None


def _previous_run(logs_dir: Path) -> PreviousRun | None:
    """Markers and dumps of processes that are gone; they are consumed here (reported once)."""
    started: datetime | None = None
    unclean = False
    for marker in logs_dir.glob("running-*.json"):
        pid = _pid_of(marker.stem)
        if pid is not None and alive(pid) and pid != os.getpid():
            continue
        unclean = True
        try:
            when = datetime.fromisoformat(json.loads(marker.read_text(encoding="utf-8"))["started"])
            started = when if started is None or when > started else started
        except (OSError, ValueError, KeyError, TypeError):
            pass
        marker.unlink(missing_ok=True)
    dump: Path | None = None
    for path in logs_dir.glob("crash-*.log"):
        pid = _pid_of(path.stem)
        if pid is None or (alive(pid) and pid != os.getpid()):
            continue  # an archived dump (not a pid), or a window that is still running
        if path.stat().st_size == 0:
            path.unlink(missing_ok=True)
            continue
        unclean = True
        dump = _archive(path)
    _prune(logs_dir)
    if not unclean:
        return None
    return PreviousRun(started, dump or latest_dump(logs_dir))


def _pid_of(stem: str) -> int | None:
    """`running-1234` / `crash-1234` -> 1234; an archived dump (`crash-20261018-120000`) has no pid."""
    tail = stem.split("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _archive(path: Path) -> Path:
    stamp = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    target = path.with_name(stamp.strftime(_ARCHIVE_FORMAT))
    n = 1
    while target.exists():
        target = path.with_name(stamp.strftime(_ARCHIVE_FORMAT).replace(".log", f"-{n}.log"))
        n += 1
    path.replace(target)
    return target


def _prune(logs_dir: Path) -> None:
    archived = sorted(p for p in logs_dir.glob("crash-????????-??????*.log"))
    for old in archived[:-KEEP_CRASH_LOGS]:
        old.unlink(missing_ok=True)


def _remove_if_empty(path: Path) -> None:
    try:
        if path.stat().st_size == 0:
            path.unlink()
    except OSError:
        pass


def _write_marker(session: _Session) -> None:
    with contextlib.suppress(OSError):  # no marker: a crash is then not reported on the next start, the dump still is
        session.marker.write_text(
            json.dumps({"pid": session.pid, "started": datetime.now(UTC).isoformat(timespec="seconds")}),
            encoding="utf-8",
        )


def _on_exception(kind: type[BaseException], value: BaseException, trace: types.TracebackType | None) -> None:
    LOGGER.critical("uncaught exception", exc_info=(kind, value, trace))
    _previous_hook(0)(kind, value, trace)


def _on_thread_exception(args: threading.ExceptHookArgs) -> None:
    if args.exc_value is not None and args.exc_type is not SystemExit:
        name = args.thread.name if args.thread is not None else "?"
        exc_info = (args.exc_type, args.exc_value, args.exc_traceback)
        LOGGER.critical("uncaught exception in thread %s", name, exc_info=exc_info)
    _previous_hook(1)(args)


def _on_unraisable(args: Any) -> None:
    exc_info = (args.exc_type, args.exc_value, args.exc_traceback)
    LOGGER.error("unraisable exception: %s", args.err_msg or "", exc_info=exc_info)
    _previous_hook(2)(args)


def _previous_hook(index: int) -> Callable[..., Any]:
    """The hook that was in place before `install` (the interpreter's own when `shutdown` already ran)."""
    originals = (sys.__excepthook__, threading.__excepthook__, sys.__unraisablehook__)
    hook = _session.hooks[index] if _session is not None else None
    return hook if callable(hook) else originals[index]
