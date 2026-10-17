"""Crash diagnostics (SPEC D85): what is left when the program dies without a word."""

from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
import textwrap
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e7ac import crashlog
from e7ac.ui.watch_signal import TkBadge, _pause_gc


@pytest.fixture
def logs(tmp_path: Path) -> Iterator[Path]:
    folder = tmp_path / "logs"
    yield folder
    crashlog.shutdown()


def _dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def _marker(logs: Path, pid: int, started: str = "2026-10-18T12:30:00+00:00") -> Path:
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"running-{pid}.json"
    path.write_text(json.dumps({"pid": pid, "started": started}), encoding="utf-8")
    return path


def test_a_run_that_ends_normally_leaves_nothing_to_report(logs: Path) -> None:
    assert crashlog.install(logs) is None
    assert (logs / f"running-{os.getpid()}.json").is_file() and crashlog.log_path(logs).is_file()
    assert crashlog.install(logs) is None  # a second call changes nothing
    crashlog.shutdown()
    assert not list(logs.glob("running-*")) and not list(logs.glob("crash-*"))  # no marker, empty dump removed
    assert crashlog.install(logs) is None  # so the next start reports nothing


def test_a_marker_of_a_process_that_is_gone_means_the_run_did_not_end_normally(logs: Path) -> None:
    marker = _marker(logs, _dead_pid())
    previous = crashlog.install(logs)
    assert previous is not None and previous.dump is None
    assert previous.started == datetime(2026, 10, 18, 12, 30, tzinfo=UTC)
    assert "did not end normally" in previous.describe() and "2026-10-18 12:30 UTC" in previous.describe()
    assert not marker.exists()  # reported once
    assert "did not end normally" in "\n".join(crashlog.recent_lines(logs))
    crashlog.shutdown()
    assert crashlog.install(logs) is None


def test_a_window_that_is_still_running_is_not_reported(logs: Path) -> None:
    marker = _marker(logs, 4242)
    with marker.open("r+b") as running:  # the other window holds its marker's lock for as long as it runs
        assert crashlog._try_lock(running)
        assert crashlog.install(logs) is None and marker.is_file()
        (logs / "crash-4242.log").write_text("", encoding="utf-8")
        crashlog.shutdown()
        assert crashlog.install(logs) is None and (logs / "crash-4242.log").is_file()  # nor its dump file
    crashlog.shutdown()
    assert crashlog.install(logs) is not None  # once that window is gone, it did not end normally


def test_a_fatal_error_dump_of_a_dead_process_is_archived_and_reported_once(logs: Path) -> None:
    pid = _dead_pid()
    logs.mkdir()
    (logs / f"crash-{pid}.log").write_text("Fatal Python error: Aborted\n", encoding="utf-8")
    (logs / f"crash-{_dead_pid()}.log").write_text("", encoding="utf-8")  # an empty one is just removed
    previous = crashlog.install(logs)
    assert previous is not None and previous.dump is not None and previous.dump.is_file()
    assert "Fatal Python error" in previous.dump.read_text(encoding="utf-8")
    assert previous.dump.name in previous.describe() and crashlog.latest_dump(logs) == previous.dump
    assert {p.name for p in logs.glob("crash-*")} == {previous.dump.name, f"crash-{os.getpid()}.log"}
    crashlog.shutdown()
    assert crashlog.install(logs) is None  # the dump is kept for reading, not reported again


def test_only_the_last_few_dumps_are_kept(logs: Path) -> None:
    logs.mkdir()
    for day in range(1, 9):
        (logs / f"crash-202610{day:02d}-120000.log").write_text("dump", encoding="utf-8")
    crashlog.install(logs)
    kept = sorted(p.name for p in logs.glob("crash-????????-??????.log"))
    assert len(kept) == crashlog.KEEP_CRASH_LOGS and kept[-1] == "crash-20261008-120000.log"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_exceptions_of_any_thread_are_logged_with_their_traceback(logs: Path) -> None:
    hooks = (sys.excepthook, threading.excepthook)
    crashlog.install(logs)

    def boom() -> None:
        raise ValueError("the badge broke")

    thread = threading.Thread(target=boom, name="e7-test-thread")
    thread.start()
    thread.join()
    try:
        raise KeyError("main thread")
    except KeyError:
        sys.excepthook(*sys.exc_info())
    text = "\n".join(crashlog.recent_lines(logs))
    assert "uncaught exception in thread e7-test-thread" in text and "ValueError: the badge broke" in text
    assert "uncaught exception" in text and "KeyError: 'main thread'" in text
    crashlog.shutdown()
    assert (sys.excepthook, threading.excepthook) == hooks  # put back


def test_the_log_stays_small(logs: Path) -> None:
    crashlog.install(logs)
    for i in range(3000):
        crashlog.note(f"line {i} " + "x" * 200)
    sizes = [p.stat().st_size for p in logs.glob("app.log*")]
    assert max(sizes) <= crashlog.LOG_MAX_BYTES + 1000 and len(sizes) <= 2


def test_a_real_fatal_error_leaves_its_stack_for_the_next_start(logs: Path) -> None:
    script = textwrap.dedent(
        f"""
        import faulthandler, pathlib
        from e7ac import crashlog
        crashlog.install(pathlib.Path({str(logs)!r}))
        def native_crash():
            faulthandler._sigsegv()
        native_crash()
        """
    )
    process = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60)
    assert process.returncode != 0
    previous = crashlog.install(logs)
    assert previous is not None and previous.dump is not None
    dump = previous.dump.read_text(encoding="utf-8", errors="replace")
    assert "native_crash" in dump  # the Python stack at the moment of the crash
    assert list(logs.glob("running-*")) == [logs / f"running-{os.getpid()}.json"]  # the dead run's marker is gone


def test_the_badge_thread_collects_its_own_garbage_and_lets_the_collector_run_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert gc.isenabled()
    assert _pause_gc() is True and not gc.isenabled()
    gc.enable()
    badge = object.__new__(TkBadge)  # no thread, no window
    state: list[bool] = []

    def loop(_self: TkBadge) -> bool:
        resumed = _pause_gc()
        state.append(gc.isenabled())
        return resumed

    monkeypatch.setattr(TkBadge, "_loop", loop)
    badge._run()
    assert state == [False] and gc.isenabled()  # paused while the window died, running again afterwards


def test_a_badge_that_fails_is_logged_and_never_leaves_the_collector_off(
    logs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crashlog.install(logs)

    def loop(_self: TkBadge) -> bool:
        raise RuntimeError("no display")

    monkeypatch.setattr(TkBadge, "_loop", loop)
    object.__new__(TkBadge)._run()
    assert gc.isenabled() and "no display" in "\n".join(crashlog.recent_lines(logs))
