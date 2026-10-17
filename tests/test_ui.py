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


def _tree(window: MainWindow) -> list[tuple[str, list[str]]]:
    nav = window._nav
    out = []
    for i in range(nav.topLevelItemCount()):
        item = nav.topLevelItem(i)
        assert item is not None
        children = [child.text(0) for j in range(item.childCount()) if (child := item.child(j)) is not None]
        out.append((item.text(0), children))
    return out


def test_the_window_builds_its_pages_on_the_first_visit(context: AppContext) -> None:
    window = MainWindow(context)
    assert _tree(window) == [("Home", []), ("Arena", []), ("\u25b8  Dev tools", ["Arena stats"])]
    assert window.page("home") is not None and window.page("arena") is None and window.page("dev") is None
    window.show_page("arena")
    assert window.page("arena") is not None and window._stack.currentWidget() is window.page("arena")
    window.show_page("dev")
    assert window.page("dev") is not None and window.page("home") is not None
    assert window.folder_open("Dev tools") and _tree(window)[2][0] == "\u25be  Dev tools"
    window.close()


def test_the_dev_tools_folder_opens_and_closes_when_clicked(context: AppContext) -> None:
    window = MainWindow(context)
    folder = window._nav.topLevelItem(2)
    assert folder is not None and not window.folder_open("Dev tools")  # closed until it is clicked
    window._nav.setCurrentItem(folder)  # as a click does
    window._clicked(folder, 0)
    assert window.folder_open("Dev tools") and window._stack.currentWidget() is window.page("home")  # page unchanged
    window._clicked(folder, 0)
    assert not window.folder_open("Dev tools") and _tree(window)[2][0].startswith("\u25b8")
    window.show_page("dev")  # going to a page inside opens its folder
    assert window.folder_open("Dev tools") and window._stack.currentWidget() is window.page("dev")
    window._nav.setCurrentItem(folder)  # a click on the folder never takes the page away
    assert window._stack.currentWidget() is window.page("dev")
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


def _fake_icons(monkeypatch: pytest.MonkeyPatch, colours: dict[str, tuple[int, int, int]]) -> None:
    """Hero icons "in the cache": solid-colour PNGs (the real ones are game art and never in the repository)."""
    import cv2
    import numpy as np

    from e7ac.ui import art

    def load(cache_dir: Any, codes: Any) -> dict[str, bytes]:
        found = {}
        for code in codes:
            if code in colours:
                picture = np.zeros((96, 96, 4), np.uint8)
                picture[:, :, :3] = colours[code][::-1]  # BGR
                picture[:, :, 3] = 255
                found[code] = cv2.imencode(".png", picture)[1].tobytes()
        return found

    monkeypatch.setattr(art, "load_hero_icons", load)


def test_portraits_are_round_with_a_gold_ring_and_never_fetched(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_icons(monkeypatch, {"c1001": (200, 30, 30)})
    portraits = context.portraits
    assert portraits.has("c1001") and not portraits.has("c1002") and portraits.cached(["c1002", "c1001"]) == ["c1001"]
    image = portraits.pixmap("c1001", 60).toImage()
    ratio = image.devicePixelRatio()
    centre = image.pixelColor(int(30 * ratio), int(30 * ratio))
    assert (centre.red(), centre.green(), centre.blue()) == (200, 30, 30)  # the icon inside
    assert image.pixelColor(0, 0).alpha() == 0  # round: the corner is empty
    ring = image.pixelColor(int(30 * ratio), int(1.5 * ratio))
    assert ring.alpha() > 0 and ring.red() > ring.blue()  # the gold rim on the top edge
    assert portraits.pixmap("c1001", 60) is portraits.pixmap("c1001", 60)  # made once
    assert not portraits.pixmap("c1002", 60).isNull() and not portraits.pixmap(None, 60).isNull()  # an empty ring
    first = portraits.pixmap("c1001", 60)
    portraits.reset()
    assert portraits.pixmap("c1001", 60) is not first  # looked up again after a reset (the icons were downloaded)


def test_the_hero_of_the_day_changes_daily_and_is_always_one_of_the_choices() -> None:
    from datetime import date

    from e7ac.ui.art import hero_of_the_day

    codes = [f"c100{i}" for i in range(1, 6)]
    today = date(2026, 10, 18)
    assert hero_of_the_day(codes, today) == hero_of_the_day(codes, today) and hero_of_the_day([], today) is None
    picks = {hero_of_the_day(codes, date.fromordinal(today.toordinal() + d)) for d in range(5)}
    assert picks == set(codes)  # every hero in turn


def test_featured_heroes_are_the_cached_ones_in_order_of_interest(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from e7ac.arena.references import ReferenceStore

    _fake_icons(monkeypatch, {c: (10, 10, 10) for c in ("c1001", "c1002", "c1003", "c1004", "c1005")})
    ReferenceStore(context.paths.home / "references" / "arena").save_last_team(["c1003", "c1009", "c1001"])
    SightingStore(sightings_path(context.paths.home)).add(
        [Sighting(datetime.now(UTC), "list", 1, ("c1004", "c1004", "c1001", "c1005"))]
    )
    assert context.featured(8) == ["c1003", "c1001", "c1004", "c1005"]  # your team (c1009 has no portrait), then Arena
    assert context.featured(2) == ["c1003", "c1001"]
    assert context.hero_of_the_day() in {"c1003", "c1001", "c1004", "c1005"}


def test_the_badge_size_is_saved_and_applied_at_once(context: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from e7ac.settings import load_settings
    from e7ac.ui.widgets import BadgeSizeBox

    resized: list[str] = []
    monkeypatch.setattr(context.watch, "set_badge_size", resized.append)
    box = BadgeSizeBox(context)
    assert box.currentData() == "large"  # the default
    box.setCurrentIndex(2)
    assert load_settings(context.paths.settings_file).badge_size.value == "huge" and resized == ["huge"]
    assert BadgeSizeBox(context).currentData() == "huge"  # remembered


def test_the_arena_page_shows_both_teams_and_the_stats_page_can_read_now(
    context: AppContext, synthetic_catalog: list[str], qapp: Any
) -> None:
    window = MainWindow(context)
    window.show_page("arena")
    window.show_page("dev")
    arena: Any = window.page("arena")
    dev: Any = window.page("dev")
    watch = context.watch
    watch.mode = ARENA
    watch.teams.emit(["c9001", "c2011"], ["c2011"])
    names = [arena._yours._layout.itemAt(i).widget().toolTip() for i in range(2)]
    assert names == ["Test Hero", "Blood Blade Karin"] and arena._theirs.codes == ["c2011"]
    watch.mode = COLLECT
    watch._state = RUNNING
    watch.state.emit(RUNNING)
    assert dev._now.isEnabled()
    watch.teams.emit(["c9001"], ["c9001"])  # a collecting watch never repaints the Arena page
    assert arena._yours.codes == ["c9001", "c2011"]
    watch._state = STOPPED
    watch.state.emit(STOPPED)
    assert not dev._now.isEnabled()
    window.close()


def test_the_home_page_offers_to_download_the_missing_portraits(
    context: AppContext, synthetic_catalog: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow(context)
    home: Any = window.page("home")
    assert not home._icons.isHidden() and "hero portraits are not on this PC" in home._icons_note.text()  # none cached
    _fake_icons(monkeypatch, {code: (1, 2, 3) for code in context.heroes()})
    context.portraits.reset()
    home.shown()
    assert home._icons.isHidden()  # everything is there: nothing to offer
    window.close()
