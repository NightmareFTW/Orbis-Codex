"""What the badge over the game looks like (SPEC D97): the drawing is looked at wherever there is a display; without
one (a headless runner) those tests skip and the pure parts still run."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest

from e7ac.ui.badge_view import SHRINK, BadgeView, mix, shorten
from e7ac.ui.watch_signal import AMBER, GREEN, LIGHT, metrics
from e7ac.vision.window import Rect


def test_mix_goes_from_one_colour_to_the_other() -> None:
    assert mix("#000000", "#ffffff", 0.0) == "#000000" and mix("#000000", "#ffffff", 1.0) == "#ffffff"
    assert mix("#102030", "#304050", 0.5) == "#203040"


def test_a_text_that_does_not_fit_is_cut_with_an_ellipsis_at_the_longest_start_that_does() -> None:
    assert shorten("Win chance 92%", lambda s: len(s) <= 20) == "Win chance 92%"
    assert shorten("Win chance 92% vs everybody", lambda s: len(s) <= 12) == "Win chance…"
    assert shorten("anything", lambda s: len(s) <= 0) == "…"


def test_a_badge_with_buttons_is_wider_and_the_height_and_fonts_are_the_same() -> None:
    plain, buttons = metrics("large", 1.0), metrics("large", 1.0, buttons=True)
    assert buttons[0] > plain[0] and buttons[1:] == plain[1:]


@pytest.fixture
def root() -> Iterator[Any]:
    tk = pytest.importorskip("tkinter")
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for tkinter")
    window.withdraw()
    yield window
    window.destroy()


def view(root: Any, buttons: list[tuple[str, Any]] | None = None) -> BadgeView:
    badge = BadgeView(root, buttons or [])
    width, height, title, line, button = metrics("large", 1.0, bool(buttons))
    badge.canvas.config(width=width, height=height)
    badge.canvas.pack()
    root.update_idletasks()
    badge.resize(width, height, title, line, button)
    return badge


def texts(badge: BadgeView) -> list[str]:
    canvas: Any = badge.canvas
    return [canvas.itemcget(i, "text") for i in canvas.find_all() if canvas.type(i) == "text"]


def test_the_badge_draws_its_two_lines_and_the_buttons(root: Any) -> None:
    pressed: list[str] = []
    badge = view(
        root, [("Check again", lambda: pressed.append("check")), ("Save picture", lambda: pressed.append("save"))]
    )
    badge.say(("Best: opponent 2 (72%) - close", GREEN), ("1: 55%  2: 72%  3: ?", LIGHT))
    shown = texts(badge)
    assert "Best: opponent 2 (72%) - close" in shown and "1: 55%  2: 72%  3: ?" in shown
    assert {"Check again", "Save picture"} <= set(shown)
    assert len(badge.canvas.find_withtag("plate0")) == 2 and len(badge.canvas.find_withtag("plate1")) == 2
    badge.say_top("Hold still...", AMBER)
    assert "Hold still..." in texts(badge) and "Best: opponent 2 (72%) - close" not in texts(badge)
    assert pressed == []  # drawing never presses anything


def test_a_badge_without_buttons_has_no_plates_and_a_long_line_is_shrunk_then_cut(root: Any) -> None:
    badge = view(root)
    long_line = "the hero name was not read: open the hero's own screen first (the name is read there)"
    badge.say(("Skills: hero not identified", "#d04040"), (long_line, LIGHT))
    assert badge.canvas.find_withtag("plate0") == ()
    drawn = [t for t in texts(badge) if t.startswith("the hero name")]
    assert drawn and (drawn[0] == long_line or drawn[0].endswith("…"))
    full = badge.font(badge.fonts[1])
    assert badge.fitted("short", badge.fonts[1], "normal", 10_000)[1].cget("size") == full.cget("size")
    text, font = badge.fitted(long_line, badge.fonts[1], "normal", 200)
    assert text.endswith("…") and abs(font.cget("size")) <= abs(full.cget("size"))
    assert abs(font.cget("size")) >= round(badge.fonts[1] * SHRINK) - 1


def _badge_picture(game: np.ndarray, rect: Rect, alpha: float = 0.92) -> np.ndarray:
    """The badge as BadgeView draws it (the panel's gradient, the gold edge), 92% opaque over the game."""
    from e7ac.ui.badge_probe import _bgr
    from e7ac.ui.badge_view import EDGE, PANEL_BOTTOM, PANEL_TOP

    out = game.astype(np.float64)
    top, bottom, gold = _bgr(PANEL_TOP), _bgr(PANEL_BOTTOM), _bgr(EDGE)
    for y in range(rect.height):
        colour = top + (bottom - top) * (y / (rect.height - 1))
        row = out[rect.top + y, rect.left : rect.left + rect.width]
        out[rect.top + y, rect.left : rect.left + rect.width] = (1 - alpha) * row + alpha * colour
    for y in (0, rect.height - 1):
        out[rect.top + y, rect.left : rect.left + rect.width] = (1 - alpha) * game[
            rect.top + y, rect.left : rect.left + rect.width
        ] + alpha * gold
    return out.round().astype(np.uint8)


def test_the_badge_is_told_in_the_picture_by_its_gradient_and_gold_edge_and_blanked() -> None:
    from e7ac.ui.badge_probe import badge_visible, hide_badge

    rng = np.random.default_rng(3)
    rect = Rect(100, 8, 220, 60)
    for game in (
        rng.integers(0, 255, (400, 640, 3)).astype(np.uint8),  # a busy game screen
        np.zeros((400, 640, 3), np.uint8),  # a black one
        np.full((400, 640, 3), 90, np.uint8),
    ):
        assert not badge_visible(game, rect)  # the badge is hidden from the capture: nothing there
        seen = _badge_picture(game, rect)
        assert badge_visible(seen, rect)
        assert not badge_visible(seen, Rect(300, 200, 220, 60))  # nor anywhere else
    seen = _badge_picture(rng.integers(0, 255, (400, 640, 3)).astype(np.uint8), rect)
    out, found = hide_badge(seen, Rect(0, 0, 640, 400), Rect(100, 8, 220, 60))
    assert found and not out[8:68, 100:320].any() and out[0:8].any()  # only the badge's place is blanked
    assert seen[8:68, 100:320].any()  # the picture handed in is not changed
    same, found = hide_badge(seen, Rect(0, 0, 640, 400), None)  # no badge: nothing to hide
    assert not found and same is seen
    shifted, found = hide_badge(seen, Rect(50, 0, 640, 400), Rect(150, 8, 220, 60))  # the game window is not at 0, 0
    assert found and not shifted[8:68, 100:320].any()
    assert not hide_badge(seen, Rect(0, 0, 640, 400), Rect(500, 300, 220, 60))[1]  # the badge is elsewhere
