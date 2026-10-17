"""The main window (SPEC D81): pages from a registry, built on first visit; the Arena and Dev pages follow the one
watch; the watch controller turns what the watch prints and says into signals. Qt runs offscreen; the tests skip
where Qt cannot start (a Linux box without libEGL)."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import typer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from e7ac.arena.sightings import Sighting, SightingStore, sightings_path  # noqa: E402
from e7ac.paths import default_paths  # noqa: E402
from e7ac.ui.context import AppContext  # noqa: E402
from e7ac.ui.pages import PageSpec  # noqa: E402
from e7ac.ui.pages.base import Page  # noqa: E402
from e7ac.ui.shell import MainWindow  # noqa: E402
from e7ac.ui.tasks import LineSink, run_captured  # noqa: E402
from e7ac.ui.watch_controller import ARENA, COLLECT, RUNNING, STOPPED, WatchController  # noqa: E402


@pytest.fixture
def qapp() -> Any:
    return widgets.QApplication.instance() or widgets.QApplication([])


@pytest.fixture
def context(qapp: Any) -> Iterator[AppContext]:
    paths = default_paths()
    paths.ensure()
    yield AppContext(paths, WatchController())


def _wait(qapp: Any, done: Any, seconds: float = 5.0) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end and not done():
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert done(), "timed out"


def test_the_window_builds_its_pages_on_the_first_visit(context: AppContext) -> None:
    window = MainWindow(context)
    labels = [window._nav.item(i).text() for i in range(window._nav.count())]
    assert labels == ["Home", "Arena", "DEV TOOLS", "Arena stats"]
    assert window.page("home") is not None and window.page("arena") is None and window.page("dev") is None
    window.show_page("arena")
    assert window.page("arena") is not None and window._stack.currentWidget() is window.page("arena")
    window.show_page("dev")
    assert window.page("dev") is not None and window.page("home") is not None
    window.close()


def test_a_new_page_is_one_registry_entry(context: AppContext) -> None:
    visits: list[str] = []

    class Extra(Page):
        def shown(self) -> None:
            visits.append("extra")

    window = MainWindow(context, [PageSpec("home", "Home", Extra), PageSpec("more", "More", Extra, group="Later")])
    assert visits == ["extra"]  # the first page is shown at start
    window.show_page("more")
    window.show_page("home")
    assert visits == ["extra", "extra", "extra"]
    window.close()


def test_the_dev_page_lists_the_heroes_met_most_and_copies_the_list(
    context: AppContext, synthetic_catalog: list[str]
) -> None:
    store = SightingStore(sightings_path(context.paths.home))
    now = datetime.now(UTC)
    old = now - timedelta(days=30)
    store.add(
        [
            Sighting(now, "list", 1, ("c9001", "c2011", None, None)),
            Sighting(now, "list", 2, ("c9001", None, None, None)),
            Sighting(old, "list", 1, ("c2011", None, None, None)),
        ]
    )
    window = MainWindow(context)
    window.show_page("dev")
    page: Any = window.page("dev")
    table = page._table
    assert [table.item(r, 1).text() for r in range(table.rowCount())] == [
        "Blood Blade Karin",
        "Test Hero",
    ]  # tied: by code
    assert [table.item(r, 3).text() for r in range(table.rowCount())] == ["2", "2"]
    page._period.setCurrentIndex(1)  # the last 24 hours: the old sighting is gone
    assert [table.item(r, 3).text() for r in range(table.rowCount())] == ["2", "1"]
    assert "2 teams in 1 refresh " in page._summary.text() and "not recognised: 62%" in page._summary.text()
    assert "Test Hero" in page.text_list() and "[?]" in page.text_list()
    window.close()


def test_the_pages_follow_the_one_watch(context: AppContext, qapp: Any) -> None:
    window = MainWindow(context)
    window.show_page("arena")
    window.show_page("dev")
    arena: Any = window.page("arena")
    dev: Any = window.page("dev")
    watch = context.watch
    watch.mode = ARENA
    watch.state.emit(RUNNING)
    watch._state = RUNNING  # as `start` leaves it
    watch.state.emit(RUNNING)
    assert arena._start.text() == "Stop" and arena._again.isEnabled() and not dev._start.isEnabled()
    watch.said.emit("Win chance 70%", "vs Test Hero", "#2fa84f")
    assert arena._top.text() == "Win chance 70%" and "70%" not in dev._status.text()
    watch.question.emit(2)
    assert not arena._ask.isHidden() and "2" in arena._question.text()
    arena._name.setText("  Blood Blade Karin ")
    arena._answer()
    assert watch._answers.get_nowait() == "Blood Blade Karin" and arena._ask.isHidden()
    watch._state = STOPPED
    watch.state.emit(STOPPED)
    assert arena._start.text() == "Start" and dev._start.isEnabled()
    window.close()


def test_the_watch_controller_turns_the_watch_into_signals(
    context: AppContext, qapp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from e7ac.cli import arena as arena_cli

    def fake_watch(options: Any, badge: Any, answers: Any, retry: Any, share: Any, stop: Any) -> None:
        typer.echo("Watching")
        badge.say("Win chance 70%", "vs Test Hero", "#2fa84f")
        options.recorded(3)
        options.ask(2)
        stop.wait(5)

    monkeypatch.setattr(arena_cli, "run_watch", fake_watch)
    watch = context.watch
    seen: dict[str, list[Any]] = {"line": [], "said": [], "state": [], "recorded": [], "question": []}
    for name, items in seen.items():
        getattr(watch, name).connect(lambda *args, items=items: items.append(args if len(args) > 1 else args[0]))
    assert watch.start(offline=True, collect_only=True, overlay=False) and watch.mode == COLLECT
    assert not watch.start(offline=True, collect_only=False, overlay=False)  # one at a time
    _wait(qapp, lambda: seen["question"])
    assert "Watching" in seen["line"] and ("Win chance 70%", "vs Test Hero", "#2fa84f") in seen["said"]
    assert seen["recorded"] == [3] and seen["question"] == [2] and seen["state"][0] == RUNNING
    watch.stop()
    _wait(qapp, lambda: seen["state"][-1] == STOPPED)
    assert not watch.running


def test_a_watch_that_fails_says_why(context: AppContext, qapp: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from e7ac.cli import arena as arena_cli

    def broken(*args: Any) -> None:
        typer.echo("Error: no catalog yet: run 'e7 catalog sync' first", err=True)
        raise typer.Exit(code=2)

    monkeypatch.setattr(arena_cli, "run_watch", broken)
    reasons: list[str] = []
    lines: list[str] = []
    context.watch.failed.connect(reasons.append)
    context.watch.line.connect(lines.append)
    context.watch.start(offline=True, collect_only=False, overlay=False)
    _wait(qapp, lambda: reasons)
    assert "code 2" in reasons[0] and any("no catalog yet" in line for line in lines)
    _wait(qapp, lambda: not context.watch.running)


def test_output_capture_is_per_line_and_one_job_at_a_time() -> None:
    lines: list[str] = []
    sink = LineSink(lines.append)
    sink.write("one\ntw")
    sink.write("o\r\nthree")
    sink.flush()
    assert lines == ["one", "two", "three"]
    inner: list[str] = []

    def work() -> None:
        typer.echo("outer")
        inner.append(run_captured(lambda: typer.echo("inner"), lambda line: None))

    out: list[str] = []
    assert run_captured(work, out.append) == ""
    assert out == ["outer"] and "another job is running" in inner[0]

    def divide() -> None:
        _ = 1 / 0

    assert run_captured(divide, out.append).startswith("ZeroDivisionError")
