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


def test_a_screen_is_read_once_when_it_stops_changing() -> None:
    watcher = ScreenWatcher()
    a, b = thumbnail(image(30)), thumbnail(image(200))
    assert [watcher.feed(t) for t in (a, a, a, b, b, b, a, a)] == [
        False,  # first frame: not known to be settled yet
        True,  # settled: read it
        False,  # already read
        False,  # changing
        True,  # a new settled screen
        False,
        False,
        True,  # back to the first screen: read again (the hero may have changed meanwhile)
    ]


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

    def sleep(seconds: float) -> None:
        state["sleeps"] += 1
        if state["sleeps"] >= state["limit"]:
            raise KeyboardInterrupt

    monkeypatch.setattr(roster_cli, "watch_sleep", sleep)
    monkeypatch.setattr(capture_cli, "prepare_process", lambda: None)
    monkeypatch.setattr(capture_cli, "window_lister", lambda: [window()])
    monkeypatch.setattr(
        roster_cli, "reader_factory", lambda: FakeReader(equipment_screen(rows=BBK_ROWS, name="Blood Blade Karin"))
    )
    yield state


def test_watch_reads_and_stores_each_new_hero_screen(
    watching: dict[str, int], monkeypatch: pytest.MonkeyPatch, synthetic_catalog: list[str]
) -> None:
    monkeypatch.setattr(capture_cli, "backend_factory", lambda: FrameQueue([10, 10, 10, 200, 200, 200, 200, 200]))
    result = runner.invoke(app, ["roster", "watch"])
    assert result.exit_code == 0, result.output
    assert result.stdout.count("Blood Blade Karin:") == 2  # two settled screens
    assert "Saved as new roster hero #1" in result.stdout and "#1: unchanged" in result.stdout
    assert "Stopped. 2 hero screen(s) read, 2 stored or unchanged." in result.stdout


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
    monkeypatch.setattr(capture_cli, "backend_factory", lambda: FrameQueue([10, 10, 10]))
    result = runner.invoke(app, ["roster", "watch"])
    assert result.exit_code == 0 and "0 hero screen(s) read" in result.stdout


def test_watch_needs_a_catalog(watching: dict[str, int]) -> None:
    result = runner.invoke(app, ["roster", "watch"])
    assert result.exit_code == 2 and "needs the catalog" in result.stderr
