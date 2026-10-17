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

from e7ac.arena.questions import FaceQuestion  # noqa: E402
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
    assert _tree(window) == [("Home", []), ("Arena", []), ("\u25b8  Dev tools", ["Arena stats"]), ("Settings", [])]
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


def test_the_pages_follow_the_one_watch(context: AppContext, synthetic_catalog: list[str], qapp: Any) -> None:
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
    asked = FaceQuestion("guide", "Opponent hero 2", "from the Attack Guide", None, None, (), ())
    watch.question.emit(asked)
    assert not arena._card.isHidden() and arena._card.current is asked and dev._card.isHidden()
    arena._card._name.setText("  Blood Blade Karin ")
    arena._card._typed()
    assert watch._answers.get_nowait() == "c2011" and arena._card.isHidden()  # the code, found from the name
    watch._state = STOPPED
    watch.state.emit(STOPPED)
    assert arena._start.text() == "Start" and dev._start.isEnabled()
    window.close()


def test_the_watch_controller_turns_the_watch_into_signals(
    context: AppContext, qapp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from e7ac.cli import arena as arena_cli

    asked = FaceQuestion("guide", "Opponent hero 2", "from the Attack Guide", None, None, (), ("c9001",))

    def fake_watch(options: Any, badge: Any, answers: Any, retry: Any, share: Any, stop: Any) -> None:
        typer.echo("Watching")
        badge.say("Win chance 70%", "vs Test Hero", "#2fa84f")
        options.recorded(3)
        options.ask_face(asked)
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
    assert seen["recorded"] == [3] and seen["question"] == [asked] and seen["state"][0] == RUNNING
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


def _question(
    kind: str = "slot", suggestions: tuple[str, ...] = ("c9001", "c2011"), title: str = "Your team, slot 2"
) -> Any:
    import numpy as np

    crop = np.full((64, 64, 3), (40, 90, 200), np.uint8)
    return FaceQuestion(kind, title, "light knight", crop, "c9001", (), suggestions)


def test_the_question_card_offers_the_likeliest_heroes_to_click(
    context: AppContext, synthetic_catalog: list[str], qapp: Any
) -> None:
    from e7ac.ui.questions import QuestionCard

    card = QuestionCard(context)
    answers: list[tuple[Any, str]] = []
    card.answered.connect(lambda q, code: answers.append((q, code)))
    asked = _question()
    card.add([asked])
    assert not card.isHidden() and card.current is asked and "Who is this?" in card._title.text()
    buttons = card.choices()
    assert [b.text().replace("\n", " ") for b in buttons] == ["Test Hero", "Blood Blade Karin"]  # in their order
    assert [b.toolTip() for b in buttons] == ["Test Hero (c9001)", "Blood Blade Karin (c2011)"]
    assert card._typing.isHidden() and not card._none.isHidden()  # the name only if none of them fits
    buttons[1].click()
    assert answers == [(asked, "c2011")] and card.isHidden()  # one click: answered


def test_the_name_is_asked_only_when_no_suggestion_fits(
    context: AppContext, synthetic_catalog: list[str], qapp: Any
) -> None:
    from e7ac.ui.questions import QuestionCard

    card = QuestionCard(context)
    answers: list[tuple[Any, str]] = []
    card.answered.connect(lambda q, code: answers.append((q, code)))
    card.add([_question(suggestions=("c9001",))])
    card._none.click()  # none of them: now the name
    assert not card._typing.isHidden()
    card._name.setText("Nobody Atall")
    card._typed()
    assert "not a hero of the catalog" in card._problem.text() and answers == []  # never a guess
    card._name.setText("blood blade karin")  # any case; or the code
    card._typed()
    assert answers[0][1] == "c2011"
    card.add([_question(suggestions=())])  # nothing to suggest: straight to the name
    assert not card._typing.isHidden() and card._none.isHidden() and card._hint.isHidden()
    card._name.setText("c9001")
    card._typed()
    assert answers[1][1] == "c9001"


def test_questions_wait_their_turn_and_can_be_skipped_one_or_all(
    context: AppContext, synthetic_catalog: list[str], qapp: Any
) -> None:
    from e7ac.ui.questions import QuestionCard

    card = QuestionCard(context)
    answers: list[tuple[Any, str]] = []
    card.answered.connect(lambda q, code: answers.append((q, code)))
    first, second, third = _question(title="one"), _question("list", title="two"), _question("list", title="three")
    card.add([first, second, third])
    assert card.pending() == 3 and card._more.text() == "+2 more" and card.property("urgent") is True
    card._answer("")  # skip the first
    assert answers == [(first, "")] and card.current is second and card.property("urgent") is False  # list: calmer
    card._drop_all()
    assert [a[1] for a in answers] == ["", "", ""] and card.isHidden() and card.pending() == 0
    card.add([_question(title="again")])
    card.clear()  # the watch stopped: nothing is answered, nothing is left
    assert card.isHidden() and len(answers) == 3


def test_the_vs_screen_question_waits_for_the_window_and_the_stop_frees_it(
    context: AppContext, qapp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    watch = context.watch
    shown: list[Any] = []
    watch.question.connect(shown.append)
    asked = _question()
    result: list[str | None] = []
    waiting = threading.Thread(target=lambda: result.append(watch._choose(asked)), daemon=True)
    waiting.start()
    _wait(qapp, lambda: shown)
    assert result == []  # the watch is waiting for the window
    watch.respond(asked, "c2011")
    waiting.join(3)
    assert result == ["c2011"]
    again = threading.Thread(target=lambda: result.append(watch._choose(asked)), daemon=True)
    again.start()
    watch.respond(asked, "")  # skipped
    again.join(3)
    assert result[-1] is None
    last = threading.Thread(target=lambda: result.append(watch._choose(asked)), daemon=True)
    last.start()
    watch._stop.set()  # the watch is stopped while it waits: it does not hang
    last.join(3)
    assert result[-1] is None and not last.is_alive()
    watch._stop.clear()
    teach = _question("list")
    watch.respond(teach, "c9001")
    watch.respond(teach, "")  # a skipped list face is not a lesson
    assert watch._taught.get_nowait() == (teach, "c9001") and watch._taught.empty()


def _settings_page(context: AppContext) -> Any:
    window = MainWindow(context)
    window.show_page("settings")
    page: Any = window.page("settings")
    assert page is not None
    return window, page


def test_settings_come_after_the_dev_tools_and_show_what_is_saved(context: AppContext) -> None:
    from e7ac.settings import load_settings

    window, page = _settings_page(context)
    assert [spec.key for spec in __import__("e7ac.ui.pages", fromlist=["PAGES"]).PAGES][-1] == "settings"
    editors = page._editors
    assert editors["client"].currentData() == "stove_pc" and editors["world"].currentData() == "world_global"
    assert editors["resolution"].text() == "" and editors["compare_runs"].value() == 3000
    assert editors["badge_overlay"].isChecked() and not editors["offline"].isChecked()
    assert not context.paths.settings_file.exists()  # showing the settings never writes them
    editors["world"].setCurrentIndex(editors["world"].findData("world_eu"))
    editors["offline"].setChecked(True)
    editors["compare_runs"].setValue(5000)
    saved = load_settings(context.paths.settings_file)
    assert saved.world.value == "world_eu" and saved.offline and saved.compare_runs == 5000
    assert page._note.text() == "Saved."
    page.shown()  # the next visit shows the same
    assert editors["world"].currentData() == "world_eu" and editors["compare_runs"].value() == 5000
    window.close()


def test_a_resolution_is_checked_before_it_is_saved(context: AppContext) -> None:
    from e7ac.settings import load_settings

    window, page = _settings_page(context)
    edit = page._editors["resolution"]
    edit.setText("banana")
    edit.editingFinished.emit()
    assert page._note.text().startswith("Not saved") and edit.text() == ""  # back to what is saved
    assert not context.paths.settings_file.exists()
    edit.setText("2560 x 1494")
    edit.editingFinished.emit()
    assert load_settings(context.paths.settings_file).resolution == "2560x1494"
    edit.setText("")
    edit.editingFinished.emit()
    assert load_settings(context.paths.settings_file).resolution is None  # empty = auto
    window.close()


def test_a_broken_settings_file_is_shown_and_never_replaced(context: AppContext) -> None:
    context.paths.settings_file.write_text("{oops", encoding="utf-8")
    window, page = _settings_page(context)
    assert "cannot be read" in page._note.text() and not page._editors["world"].isEnabled()
    page._save("world", "world_eu")
    assert context.paths.settings_file.read_text(encoding="utf-8") == "{oops"
    window.close()


def test_the_badge_size_set_in_the_settings_is_applied_at_once(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    resized: list[str] = []
    monkeypatch.setattr(context.watch, "set_badge_size", resized.append)
    window, page = _settings_page(context)
    box = page._editors["badge_size"]
    box.setCurrentIndex(box.findData("huge"))
    assert resized == ["huge"]
    window.close()


def test_the_self_check_lives_in_the_settings_not_on_the_home_page(
    context: AppContext, qapp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from e7ac.doctor import CheckResult, CheckStatus

    monkeypatch.setattr(
        "e7ac.ui.pages.settings.run_checks",
        lambda paths: [
            CheckResult("ocr", CheckStatus.OK, "ready"),
            CheckResult("game window", CheckStatus.WARN, "none"),
        ],
    )
    window, page = _settings_page(context)
    _wait(qapp, lambda: page._checks.itemAtPosition(1, 1) is not None)
    texts = [page._checks.itemAtPosition(i, c).widget().text() for i in range(2) for c in range(2)]
    assert texts == ["OK   ocr", "ready", "WARN game window", "none"]
    home: Any = window.page("home")
    assert not hasattr(home, "_checks") and not hasattr(home, "_runner")
    window.close()


def test_kept_pictures_are_counted_and_can_be_deleted(context: AppContext) -> None:
    window, page = _settings_page(context)
    assert page._kept.text() == "No kept pictures." and not page._forget.isEnabled()
    folder = context.paths.captures_dir / "arena" / "debug"
    folder.mkdir(parents=True)
    for name in ("a.png", "b.png"):
        (folder / name).write_bytes(b"x" * 1000)
    shared = context.paths.captures_dir / "arena" / "shared"
    shared.mkdir()
    (shared / "keep.png").write_bytes(b"y")
    page.shown()
    assert page._kept.text().startswith("2 kept pictures") and page._forget.isEnabled()
    assert page.forget_pictures() == 2
    assert page._kept.text() == "No kept pictures." and (shared / "keep.png").is_file()  # only the kept ones go
    window.close()


def test_the_diagnostics_say_how_the_last_run_ended_and_make_a_report(context: AppContext) -> None:
    from e7ac import __version__
    from e7ac.crashlog import PreviousRun

    window, page = _settings_page(context)
    assert page._last_run.text() == "The last run ended normally."
    context.previous = PreviousRun(datetime(2026, 10, 18, 12, 30, tzinfo=UTC), None)
    page.shown()
    assert "2026-10-18 12:30 UTC" in page._last_run.text() and "did not end normally" in page._last_run.text()
    report = page.report()
    assert __version__ in report and "world_global" in report and "did not end normally" in report
    window.close()


def test_the_home_page_warns_when_the_last_run_ended_unexpectedly(context: AppContext) -> None:
    from e7ac.crashlog import PreviousRun

    context.previous = PreviousRun(None, None)
    window = MainWindow(context)
    home: Any = window.page("home")
    assert not home._crash.isHidden() and "did not end normally" in home._crash_text.text()
    home._dismiss_crash()
    assert home._crash.isHidden() and getattr(context, "previous") is None  # noqa: B009 (mypy narrowed it)
    window.close()


def test_the_icon_is_celines_portrait_or_the_users_own_picture_or_a_plain_emblem(
    context: AppContext, qapp: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from PySide6.QtGui import QColor, QImage

    from e7ac.ui import icon

    paths = context.paths
    assert icon.source(paths) == "the plain emblem (no portraits yet)"
    assert not icon.app_icon(paths).isNull() and 256 in {s.width() for s in icon.app_icon(paths).availableSizes()}
    _fake_icons(monkeypatch, {icon.ICON_HERO: (200, 30, 30)})  # Celine is "in the cache"
    assert icon.source(paths) == "Celine's portrait"
    # the user's own picture: not square, only its middle is kept
    wide = QImage(300, 100, QImage.Format.Format_RGB32)
    wide.fill(QColor(0, 0, 255))
    for x in range(100):  # the left and right thirds are other colours
        for y in range(100):
            wide.setPixelColor(x, y, QColor(255, 0, 0))
            wide.setPixelColor(200 + x, y, QColor(255, 0, 0))
    source = tmp_path / "skin.png"
    assert wide.save(str(source))
    assert icon.use_picture(paths, source) == ""
    kept = QImage(str(icon.override_path(paths)))
    assert (kept.width(), kept.height()) == (100, 100) and kept.pixelColor(50, 50).blue() == 255
    assert icon.source(paths) == "your own picture"
    centre = icon.app_icon(paths).pixmap(64, 64).toImage()
    ratio = centre.devicePixelRatio()
    assert centre.pixelColor(int(32 * ratio), int(32 * ratio)).blue() > 200
    # what is not a picture changes nothing
    (tmp_path / "notes.txt").write_text("not a picture", encoding="utf-8")
    assert "not a picture" in icon.use_picture(paths, tmp_path / "notes.txt")
    assert icon.source(paths) == "your own picture"
    assert icon.use_default(paths) == "" and icon.source(paths) == "Celine's portrait"


def test_the_settings_page_changes_the_icon_and_the_window_follows(
    context: AppContext, qapp: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from PySide6.QtGui import QColor, QImage

    window, page = _settings_page(context)
    assert (
        page._icon_source.text() == "Program icon: the plain emblem (no portraits yet)."
        and not page._icon_default.isEnabled()
    )
    face = QImage(80, 80, QImage.Format.Format_RGB32)
    face.fill(QColor(0, 200, 0))
    path = tmp_path / "face.png"
    assert face.save(str(path))
    page.use_icon(path)
    assert page._icon_source.text() == "Program icon: your own picture." and page._icon_default.isEnabled()
    assert page._note.text() == "The icon is changed." and not qapp.windowIcon().isNull()
    page.use_default_icon()
    assert page._icon_source.text().startswith("Program icon: the plain emblem") and not page._icon_default.isEnabled()
    page.use_icon(tmp_path / "missing.png")
    assert page._note.text().startswith("The icon was not changed")
    window.close()


def test_the_controller_hands_the_comparison_to_the_window_and_the_settings_to_the_watch(
    context: AppContext, qapp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from e7ac.arena.compare import OpponentOdds
    from e7ac.cli import arena as arena_cli

    rows = [OpponentOdds(1, ("c1001",) * 4, 0.7, 0.02, "", best=True)]
    got: dict[str, Any] = {}

    def fake_watch(options: Any, badge: Any, answers: Any, retry: Any, share: Any, stop: Any) -> None:
        got["runs"] = options.compare_runs
        options.opponents(rows)
        stop.wait(5)

    monkeypatch.setattr(arena_cli, "run_watch", fake_watch)
    seen: list[Any] = []
    context.watch.opponents.connect(seen.append)
    assert context.watch.start(offline=True, collect_only=False, overlay=False, compare_runs=2000)
    _wait(qapp, lambda: seen)
    assert got["runs"] == 2000 and seen == [rows]
    context.watch.stop()
    _wait(qapp, lambda: not context.watch.running)


def test_the_arena_page_lists_the_three_opponents_with_the_best_marked_and_remembers_its_options(
    context: AppContext, qapp: Any
) -> None:
    from e7ac.arena.compare import OpponentOdds, mark_best
    from e7ac.settings import load_settings

    team = ("c1001", "c1002", "c1003", "c1004")
    rows = mark_best(
        [
            OpponentOdds(1, team, 0.55, 0.02, ""),
            OpponentOdds(2, ("c1005", None, "c1006", "c1007"), None, None, "a hero is not recognised"),
            OpponentOdds(3, team, 0.82, 0.02, ""),
        ]
    )
    window = MainWindow(context)
    window.show_page("arena")
    arena: Any = window.page("arena")
    assert arena._opponents.isHidden()
    context.watch.mode = ARENA
    context.watch.opponents.emit(rows)
    card = arena._opponents
    assert not card.isHidden() and [r.isHidden() for r in card._rows] == [False, False, False]
    assert [r.tag.text() for r in card._rows] == ["", "", "★ best"]
    assert [r.chance.text() for r in card._rows] == ["55% (+/-2%)", "a hero is not recognised", "82% (+/-2%)"]
    assert card._rows[1].heroes.codes == ["c1005", None, "c1006", "c1007"]  # the face nobody could name stays empty
    context.watch.mode = COLLECT  # a collecting watch has no use for it
    context.watch.opponents.emit(rows[:1])
    assert len(card._rows[0].chance.text()) and card._rows[2].tag.text() == "★ best"
    context.watch._state = STOPPED
    context.watch.state.emit(STOPPED)
    assert card.isHidden()
    arena._offline.setChecked(True)  # remembered for the next start
    assert load_settings(context.paths.settings_file).offline
    arena._overlay.setChecked(True)  # (off to begin with where there is no badge: not Windows)
    arena._overlay.setChecked(False)
    assert not load_settings(context.paths.settings_file).badge_overlay
    window.close()


def test_the_icon_is_also_a_windows_icon_file_for_shortcuts(
    context: AppContext, qapp: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import struct

    from PySide6.QtGui import QColor, QImage

    from e7ac.ui import icon

    _fake_icons(monkeypatch, {icon.ICON_HERO: (200, 30, 30)})
    paths = context.paths
    ico = icon.write_ico(paths)
    assert ico is not None and ico == icon.ico_path(paths) and ico.name == "orbis.ico"
    data = ico.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, len(icon.ICO_SIZES))  # an icon file with one picture per size
    sizes = []
    for i in range(count):
        width, height, _c, _r, _planes, bits, length, offset = struct.unpack(
            "<BBBBHHII", data[6 + 16 * i : 22 + 16 * i]
        )
        assert width == height and bits == 32 and data[offset : offset + 8] == b"\x89PNG\r\n\x1a\n"
        picture = QImage.fromData(data[offset : offset + length])
        assert picture.width() == (width or 256) and picture.pixelColor(0, 0).alpha() == 0  # round, exact pixels
        sizes.append(width or 256)
    assert sizes == list(icon.ICO_SIZES)
    first = ico.stat().st_mtime_ns
    assert icon.write_ico(paths) == ico and ico.stat().st_mtime_ns == first  # unchanged: not written again
    face = QImage(40, 40, QImage.Format.Format_RGB32)
    face.fill(QColor(0, 200, 0))
    path = tmp_path / "face.png"
    assert face.save(str(path)) and icon.use_picture(paths, path) == ""
    assert icon.write_ico(paths) == ico and ico.read_bytes() != data  # the user's own picture is in it now


def test_the_program_icon_is_in_the_window_at_the_sizes_icons_have(
    context: AppContext, qapp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from e7ac.ui import icon

    _fake_icons(monkeypatch, {icon.ICON_HERO: (200, 30, 30)})
    icon.install(qapp, context.paths)
    window = MainWindow(context)
    window.show()
    qapp.processEvents()
    handle = window.windowHandle()
    assert handle is not None and not handle.icon().isNull()  # what the platform gives the taskbar
    assert {s.width() for s in qapp.windowIcon().availableSizes()} >= {16, 24, 32, 48, 256}
    assert icon.ico_path(context.paths).is_file()  # and the file a shortcut reads
    window.close()


def test_the_settings_page_makes_the_desktop_shortcut(
    context: AppContext, qapp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    from e7ac import shortcut

    monkeypatch.setattr(shortcut, "on_windows", lambda: False)
    window, page = _settings_page(context)
    assert not page._shortcut.isEnabled()  # a Windows thing
    made: list[Any] = []

    def create(working_dir: Path, ico: Path) -> Path:
        made.append((working_dir, ico))
        return Path("C:/Desktop/Orbis Codex.lnk")

    monkeypatch.setattr(shortcut, "create_desktop_shortcut", create)
    page.make_shortcut()
    assert made[0][0] == context.paths.home and made[0][1].name == "orbis.ico" and made[0][1].is_file()
    assert page._note.text().startswith("The shortcut is on your Desktop")

    def refuse(working_dir: Path, ico: Path) -> Path:
        raise shortcut.ShortcutError("PowerShell did not make the shortcut: access denied")

    monkeypatch.setattr(shortcut, "create_desktop_shortcut", refuse)
    page.make_shortcut()
    assert page._note.text() == "The shortcut was not made: PowerShell did not make the shortcut: access denied"
    window.close()
