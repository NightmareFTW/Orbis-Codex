"""Passive watch (M9a): settled-screen detection and `e7 roster watch` with fake windows, frames and OCR."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pytest
from typer.testing import CliRunner

from e7ac.cli import capture as capture_cli
from e7ac.cli import roster as roster_cli
from e7ac.cli.app import app
from e7ac.paths import default_paths
from e7ac.vision.capture import Frame
from e7ac.vision.watch import ScreenWatcher, differs, thumbnail
from e7ac.vision.window import Rect, WindowError, WindowInfo
from tests.test_hero_screen import BBK_ROWS, FakeReader, equipment_screen
from tests.test_vision import window

runner = CliRunner()


def image(level: int, size: tuple[int, int] = (36, 64)) -> np.ndarray:
    return np.full((*size, 3), level, dtype=np.uint8)


def test_a_screen_is_read_once_it_stayed_still_for_the_settle_time() -> None:
    watcher = ScreenWatcher(settle=1.5)
    a, b = thumbnail(image(30)), thumbnail(image(200))
    frames = [(a, 0.0), (a, 0.5), (a, 1.0), (a, 1.5), (a, 2.0), (b, 2.5), (b, 3.0), (b, 4.0), (a, 4.5), (a, 6.0)]
    assert [watcher.feed(t, now) for t, now in frames] == [
        False,  # first sight: the game may still be rolling numbers up
        False,
        False,
        True,  # still for 1.5 s: read it
        False,  # already read
        False,  # a new screen...
        False,
        True,  # ...read once it stayed still for 1.5 s
        False,
        True,  # back to the first screen: read again (the hero may have changed meanwhile)
    ]


def test_a_slow_fade_counts_as_a_change() -> None:
    watcher = ScreenWatcher(threshold=0.04, settle=1.0)
    steps = [thumbnail(image(level)) for level in (100, 107, 114, 121, 128)]  # each step below the threshold
    assert not any(watcher.feed(t, i * 0.5) for i, t in enumerate(steps))  # the drift adds up: the timer restarts


def test_small_changes_such_as_an_idle_animation_are_not_a_new_screen() -> None:
    base = image(100, (360, 640))
    moved = base.copy()
    moved[100:140, 300:340] = 255  # a small area changes
    assert not differs(thumbnail(base), thumbnail(moved))
    assert differs(thumbnail(base), thumbnail(image(160, (360, 640))))


class RecordingSignal:
    """The watch badge, recorded."""

    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def place(self, area: Rect) -> None:
        if not self.events or self.events[-1] != ("place", area):
            self.events.append(("place", area))

    def settling(self) -> None:
        self.events.append(("settling",))

    def captured(self, waiting: int) -> None:
        self.events.append(("captured", waiting))

    def result(self, name: str, outcome: str, waiting: int) -> None:
        self.events.append(("result", name, outcome, waiting))

    def region(self) -> None:
        return None

    def close(self) -> None:
        self.events.append(("close",))


SIGNALS = RecordingSignal()


class FrameQueue:
    """A capture backend that returns prepared frames, then raises KeyboardInterrupt through the sleep hook."""

    name = "fake"

    def __init__(self, levels: list[int]) -> None:
        self.levels = levels

    def grab(self, win: WindowInfo) -> Frame:
        level = self.levels.pop(0) if self.levels else 0
        return Frame(
            win.client.width,
            win.client.height,
            bytes([level]) * (win.client.width * win.client.height * 3),
            "fake",
            win,
        )


@pytest.fixture
def watching(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, int]]:
    state = {"sleeps": 0, "limit": 8}
    clock = {"now": 0.0}

    def sleep(seconds: float) -> None:
        clock["now"] += seconds
        state["sleeps"] += 1
        if state["sleeps"] >= state["limit"]:
            raise KeyboardInterrupt

    monkeypatch.setattr(roster_cli, "watch_sleep", sleep)
    monkeypatch.setattr(roster_cli, "watch_clock", lambda: clock["now"])
    monkeypatch.setattr(roster_cli, "signal_factory", lambda beep: (SIGNALS, ""))
    SIGNALS.events.clear()
    monkeypatch.setattr(capture_cli, "prepare_process", lambda: None)
    monkeypatch.setattr(capture_cli, "window_lister", lambda: [window()])
    monkeypatch.setattr(
        roster_cli, "reader_factory", lambda: FakeReader(equipment_screen(rows=BBK_ROWS, name="Blood Blade Karin"))
    )
    yield state


def test_watch_reads_and_stores_each_new_hero_screen(
    watching: dict[str, int], monkeypatch: pytest.MonkeyPatch, synthetic_catalog: list[str]
) -> None:
    levels = [10] * 5 + [200] * 5  # each screen shown for 2.5 s at one capture per 0.5 s
    monkeypatch.setattr(capture_cli, "backend_factory", lambda: FrameQueue(levels))
    watching["limit"] = len(levels)
    result = runner.invoke(app, ["roster", "watch"])
    assert result.exit_code == 0, result.output
    assert result.stdout.count("Blood Blade Karin:") == 2  # two settled screens
    assert "Saved as new roster hero #1" in result.stdout and "#1: unchanged" in result.stdout
    kinds = [e[0] for e in SIGNALS.events if e[0] != "place"]
    assert kinds == ["settling", "captured", "settling", "captured", "result", "result", "close"] or (
        kinds.count("captured") == 2 and kinds[-1] == "close"  # results may arrive while the next screen settles
    )
    assert ("result", "Blood Blade Karin", "stored") in [e[:3] for e in SIGNALS.events]
    assert next(e for e in SIGNALS.events if e[0] == "captured") == ("captured", 1)  # nothing else waiting
    assert "Stopped. 2 hero screen(s) read, 2 stored or unchanged, 0 incomplete screen(s) ignored." in result.stdout


def test_a_doubtful_reading_is_stored_flagged_and_its_capture_kept(
    watching: dict[str, int], monkeypatch: pytest.MonkeyPatch, synthetic_catalog: list[str]
) -> None:
    rows = [(label, final, "6943" if label == "Health" else bonus) for label, final, bonus in BBK_ROWS]  # misread
    monkeypatch.setattr(
        roster_cli, "reader_factory", lambda: FakeReader(equipment_screen(rows=rows, name="Blood Blade Karin"))
    )
    monkeypatch.setattr(capture_cli, "backend_factory", lambda: FrameQueue([10] * 6))
    result = runner.invoke(app, ["roster", "watch"])
    assert result.exit_code == 0, result.output
    assert "Saved as new roster hero #1" in result.stdout
    assert ("result", "Blood Blade Karin", "doubtful", 0) in SIGNALS.events
    kept = list((default_paths().captures_dir / "watch").glob("*-Blood_Blade_Karin.png"))
    assert len(kept) == 1 and f"capture kept: {kept[0]}" in result.stdout
    assert "1 screen(s) not stored or with a failed check: their captures are in" in result.stdout


def test_watch_waits_for_the_game_window_and_says_so_once(
    watching: dict[str, int], monkeypatch: pytest.MonkeyPatch, synthetic_catalog: list[str]
) -> None:
    def no_window() -> list[WindowInfo]:
        raise WindowError("no Epic Seven window found")

    monkeypatch.setattr(capture_cli, "window_lister", no_window)
    monkeypatch.setattr(capture_cli, "backend_factory", lambda: FrameQueue([]))
    result = runner.invoke(app, ["roster", "watch", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert result.stderr.count("waiting: no Epic Seven window found") == 1
    assert "0 hero screen(s) read" in result.stdout


def test_watch_ignores_screens_that_are_not_hero_screens(
    watching: dict[str, int], monkeypatch: pytest.MonkeyPatch, synthetic_catalog: list[str]
) -> None:
    monkeypatch.setattr(roster_cli, "reader_factory", lambda: FakeReader([]))
    monkeypatch.setattr(capture_cli, "backend_factory", lambda: FrameQueue([10] * 6))
    result = runner.invoke(app, ["roster", "watch"])
    assert result.exit_code == 0 and "0 hero screen(s) read" in result.stdout
    assert ("result", "", "not_hero", 0) in SIGNALS.events  # the "Reading..." line never stays up


def test_watch_ignores_incomplete_hero_screens(
    watching: dict[str, int], monkeypatch: pytest.MonkeyPatch, synthetic_catalog: list[str]
) -> None:
    popup = [  # a gear popup over the hero screen: no level, no CP
        line for line in equipment_screen(rows=BBK_ROWS, name="Speed Set") if line.text not in ("Lv. Max/60", "123,120")
    ]
    monkeypatch.setattr(roster_cli, "reader_factory", lambda: FakeReader(popup))
    monkeypatch.setattr(capture_cli, "backend_factory", lambda: FrameQueue([10] * 6))
    result = runner.invoke(app, ["roster", "watch"])
    assert result.exit_code == 0, result.output
    assert "Speed Set" not in result.stdout and "1 incomplete screen(s) ignored" in result.stdout
    assert ("result", "", "incomplete", 0) in SIGNALS.events


def test_watch_needs_a_catalog(watching: dict[str, int]) -> None:
    result = runner.invoke(app, ["roster", "watch"])
    assert result.exit_code == 2 and "needs the catalog" in result.stderr


def test_the_badge_is_off_with_no_signal(
    watching: dict[str, int], monkeypatch: pytest.MonkeyPatch, synthetic_catalog: list[str]
) -> None:
    monkeypatch.setattr(capture_cli, "backend_factory", lambda: FrameQueue([10] * 6))
    result = runner.invoke(app, ["roster", "watch", "--no-signal", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert SIGNALS.events == []


def test_the_watcher_says_when_to_hold_still() -> None:
    watcher = ScreenWatcher(settle=1.0)
    a, b = thumbnail(image(30)), thumbnail(image(200))
    states = []
    for thumb, now in ((a, 0.0), (a, 1.0), (b, 1.5), (b, 2.0), (b, 2.5)):
        watcher.feed(thumb, now)
        states.append(watcher.settling)
    assert states == [True, False, True, True, False]  # read at 1.0 and 2.5: no hold-still once read


@pytest.mark.parametrize(
    ("area", "excluded", "spot"),
    [
        (Rect(100, 200, 1600, 900), True, (750, 208)),  # invisible to capture: top centre of the game
        (Rect(100, 200, 1600, 900), False, (750, 136)),  # visible to capture: just above the game window
        (Rect(0, 0, 1600, 900), False, (650, 906)),  # no room above: just below
        (Rect(0, 0, 1920, 1080), False, None),  # full screen: nowhere outside the game, so not shown
    ],
)
def test_the_badge_never_covers_what_is_read(area: Rect, excluded: bool, spot: tuple[int, int] | None) -> None:
    from e7ac.ui.watch_signal import _spot

    assert _spot(area, excluded, screen_height=1080) == spot


def test_the_badge_lines_say_which_hero_a_result_is_for() -> None:
    from e7ac.ui.watch_signal import AMBER, GREEN, LIGHT, reading_line, result_line

    assert reading_line(1) == "Reading..." and reading_line(3) == "Reading... (3 waiting)"
    assert result_line("Renoa", "stored", 0) == ("Renoa: stored", GREEN)
    assert result_line("Renoa", "doubtful", 1) == ("Renoa: stored, but check the console - 1 more being read", AMBER)
    assert result_line("", "not_hero", 0) == ("Not a hero screen", LIGHT)


@pytest.mark.windows
def test_the_badge_starts_and_closes_on_windows() -> None:
    from e7ac.ui.watch_signal import open_signal

    badge, why = open_signal(beep=False)
    if why:
        pytest.skip(why)
    badge.place(Rect(100, 200, 1600, 900))
    badge.settling()
    badge.captured(2)
    badge.result("Blood Blade Karin", "stored", 1)
    badge.result("Blood Blade Karin", "doubtful", 0)
    badge.result("", "not_hero", 0)
    badge.close()


def test_the_badge_grows_with_its_size_and_with_the_screen() -> None:
    from e7ac.ui.watch_signal import DEFAULT_SIZE, HEIGHT, SIZES, WIDTH, metrics

    assert DEFAULT_SIZE == "large" and set(SIZES) == {"normal", "large", "huge"}
    assert metrics("normal", 1.0) == (WIDTH, HEIGHT, 16, 13, 11)  # the old badge, unchanged
    large, huge = metrics("large", 1.0), metrics("huge", 1.0)
    assert large[0] == round(WIDTH * 1.6) and huge[0] > large[0] > WIDTH and huge[2] > large[2] > 16
    assert metrics("large", 1.5)[0] == round(WIDTH * 1.6 * 1.5)  # a 150% screen
    assert metrics("large", 9.0) == metrics("large", 4.0) and metrics("large", 0.5) == large  # sane limits
    assert metrics("nonsense", 1.0) == large  # an unknown name is the default, never an error


def test_the_badge_is_placed_by_its_own_size() -> None:
    from e7ac.ui.watch_signal import _spot

    area = Rect(100, 40, 1000, 600)
    assert _spot(area, True, 1080, 480, 93) == (100 + (1000 - 480) // 2, 48)  # centred, whatever its width
    assert _spot(area, False, 1080, 480, 93) == (360, 40 + 600 + 6)  # no room above: below, by its own height
    assert _spot(Rect(100, 40, 1000, 1040), False, 1080, 480, 93) is None  # no room anywhere outside the game
