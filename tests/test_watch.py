"""Passive watch (M9a): settled-screen detection and `e7 roster watch` with fake windows, frames and OCR."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pytest
from typer.testing import CliRunner

from e7ac.cli import capture as capture_cli
from e7ac.cli import roster as roster_cli
from e7ac.cli.app import app
from e7ac.vision.capture import Frame
from e7ac.vision.watch import ScreenWatcher, differs, thumbnail
from e7ac.vision.window import WindowError, WindowInfo
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
    assert "Stopped. 2 hero screen(s) read, 2 stored or unchanged, 0 incomplete screen(s) ignored." in result.stdout


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


def test_watch_needs_a_catalog(watching: dict[str, int]) -> None:
    result = runner.invoke(app, ["roster", "watch"])
    assert result.exit_code == 2 and "needs the catalog" in result.stderr
