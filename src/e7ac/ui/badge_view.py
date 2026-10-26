"""What the badge over the game looks like (SPEC D97): one tkinter canvas in the game's palette.

A deep navy panel that darkens towards the bottom, a thin gold edge, a stripe and a round light in the colour of the
state at its left (green: done, amber: wait or look, red: failed, grey: nothing to do), the headline in the state's
colour and a quieter line under it, and the buttons (when there are any) as small bronze plates with a gold edge that
light up under the pointer. Everything is drawn from the badge's size, so the "normal", "large" and "huge" sizes and
a high-DPI screen only change the numbers. Pure drawing: no Windows calls, so it can be looked at anywhere there is a
display (the tests do, when there is one)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Final, Literal

from e7ac.ui.watch_signal import AMBER, GREEN, HEIGHT, LIGHT, RED, WIDTH

PANEL_TOP: Final = "#222a3d"
PANEL_BOTTOM: Final = "#0d1018"
EDGE: Final = "#8a6d3b"
EDGE_LIT: Final = "#c9a96e"
SHINE: Final = "#3a4666"
TEXT: Final = "#f2ecdc"
MUTED: Final = "#b3a688"
NEUTRAL: Final = "#8c93a3"
PLATE: Final = "#4a3414"
PLATE_LIT: Final = "#6e4d24"
FAMILY: Final = "Segoe UI"
FONT_TITLE, FONT_LINE, FONT_BUTTON = 16, 13, 11
"""Pixels at the "normal" size (`watch_signal.metrics` scales them)."""

SHRINK: Final = 0.78
"""A line that is too long for its room gets a smaller font, down to this share of its size, before it is cut."""
_HEADLINE: Final = {LIGHT: TEXT}
_LIGHT: Final = {LIGHT: NEUTRAL}
_LINE: Final = {LIGHT: MUTED}


def mix(first: str, second: str, share: float) -> str:
    """The colour `share` of the way from `first` to `second` (both "#rrggbb")."""
    a = [int(first[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(second[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * share):02x}" for x, y in zip(a, b, strict=True))


def shorten(text: str, fits: Callable[[str], bool]) -> str:
    """The text, or its start and an ellipsis when `fits` says it is too long (the line never runs under a button)."""
    if fits(text):
        return text
    low, high = 0, len(text)
    while low < high:  # the longest start that still fits, with the ellipsis
        middle = (low + high + 1) // 2
        if fits(text[:middle].rstrip() + "…"):
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + "…" if low else "…"


class BadgeView:
    """The badge's canvas: set the two lines and the state, resize it, and it draws itself again."""

    def __init__(self, parent: Any, buttons: Sequence[tuple[str, Callable[[], None]]] = ()) -> None:
        import tkinter as tk
        from tkinter import font as tkfont

        self._font = tkfont
        self.canvas = tk.Canvas(parent, highlightthickness=0, bd=0, bg=PANEL_BOTTOM)
        self.buttons = list(buttons)
        self.top, self.top_colour = "Watching the game...", LIGHT
        self.bottom, self.bottom_colour = "", LIGHT
        self.size = (WIDTH, HEIGHT)
        self.fonts = (FONT_TITLE, FONT_LINE, FONT_BUTTON)
        self._plates: list[int] = []
        self._fonts: dict[tuple[int, Literal["normal", "bold"]], Any] = {}
        """The fonts made so far, by pixel size and weight (made once: they live as long as the badge)."""
        self.canvas.bind("<Configure>", self._configured)

    # --- what it says -------------------------------------------------------------------------------------------

    def say_top(self, text: str, colour: str) -> None:
        self.top, self.top_colour = text, colour
        self.draw()

    def say_bottom(self, text: str, colour: str) -> None:
        self.bottom, self.bottom_colour = text, colour
        self.draw()

    def say(self, top: tuple[str, str], bottom: tuple[str, str]) -> None:
        """Both lines in one drawing: they never disagree on screen."""
        (self.top, self.top_colour), (self.bottom, self.bottom_colour) = top, bottom
        self.draw()

    def resize(self, width: int, height: int, title: int, line: int, button: int) -> None:
        self.size, self.fonts = (width, height), (title, line, button)
        self.draw()

    def _configured(self, event: Any) -> None:
        if event.width > 8 and event.height > 8 and (event.width, event.height) != self.size:
            self.size = (event.width, event.height)
            self.draw()

    # --- drawing ------------------------------------------------------------------------------------------------

    def font(self, pixels: int, weight: Literal["normal", "bold"] = "normal") -> Any:
        key = (max(6, pixels), weight)
        if key not in self._fonts:
            self._fonts[key] = self._font.Font(root=self.canvas, family=FAMILY, size=-key[0], weight=weight)
        return self._fonts[key]

    def fitted(self, text: str, pixels: int, weight: Literal["normal", "bold"], room: int) -> tuple[str, Any]:
        """The text in the largest font, down to `SHRINK` of the size, that holds it in `room`; shortened only when even
        the smallest does not (a headline that matters, like "- close", is read rather than cut)."""
        for size in range(pixels, max(6, round(pixels * SHRINK)) - 1, -1):
            font = self.font(size, weight)
            if font.measure(text) <= room:
                return text, font
        font = self.font(max(6, round(pixels * SHRINK)), weight)
        return shorten(text, lambda s: bool(font.measure(s) <= room)), font

    def draw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width, height = self.size
        unit = height / HEIGHT
        light = _LIGHT.get(self.top_colour, self.top_colour)
        for y in range(height):  # the panel: lighter at the top, darker at the bottom
            canvas.create_line(0, y, width, y, fill=mix(PANEL_TOP, PANEL_BOTTOM, y / max(1, height - 1)))
        canvas.create_line(3 * unit, 1, width - 3 * unit, 1, fill=SHINE)  # the light catching its upper edge
        canvas.create_rectangle(0, 0, width - 1, height - 1, outline=EDGE)
        canvas.create_rectangle(1, 1, width - 2, height - 2, outline=mix(EDGE, PANEL_BOTTOM, 0.55))
        stripe = max(3, round(5 * unit))
        canvas.create_rectangle(0, 0, stripe, height, fill=light, outline="")
        canvas.create_line(stripe, 0, stripe, height, fill=mix(light, PANEL_BOTTOM, 0.5))
        plates_width = self._draw_plates(self.font(self.fonts[2], "bold"), unit)
        left = stripe + round(30 * unit)
        room = width - left - plates_width - round(10 * unit)
        radius = round(5.5 * unit)
        centre = round(height * 0.33)
        canvas.create_oval(
            stripe + round(11 * unit) - radius - 2,
            centre - radius - 2,
            stripe + round(11 * unit) + radius + 2,
            centre + radius + 2,
            fill=mix(light, PANEL_BOTTOM, 0.65),
            outline="",
        )
        canvas.create_oval(
            stripe + round(11 * unit) - radius,
            centre - radius,
            stripe + round(11 * unit) + radius,
            centre + radius,
            fill=light,
            outline=mix(light, TEXT, 0.5),
        )
        head, title_font = self.fitted(self.top, self.fonts[0], "bold", room)
        canvas.create_text(
            left,
            centre,
            text=head,
            anchor="w",
            font=title_font,
            fill=_HEADLINE.get(self.top_colour, self.top_colour),
        )
        line, line_font = self.fitted(self.bottom, self.fonts[1], "normal", room)
        canvas.create_text(
            left,
            round(height * 0.70),
            text=line,
            anchor="w",
            font=line_font,
            fill=_LINE.get(self.bottom_colour, self.bottom_colour),
        )

    def _draw_plates(self, font: Any, unit: float) -> int:
        """The buttons as plates stacked at the right; returns the width they take (0 without buttons)."""
        self._plates = []
        if not self.buttons:
            return 0
        canvas = self.canvas
        width, height = self.size
        pad = round(7 * unit)
        plate_width = max(font.measure(label) for label, _ in self.buttons) + round(22 * unit)
        gap = max(2, round(3 * unit))
        each = (height - 2 * pad - gap * (len(self.buttons) - 1)) / len(self.buttons)
        x1 = width - pad
        x0 = x1 - plate_width
        for index, (label, action) in enumerate(self.buttons):
            y0 = pad + index * (each + gap)
            tag = f"plate{index}"
            plate = canvas.create_rectangle(x0, y0, x1, y0 + each, fill=PLATE, outline=EDGE_LIT, tags=(tag,))
            canvas.create_text((x0 + x1) / 2, y0 + each / 2, text=label, font=font, fill=TEXT, tags=(tag,))
            self._plates.append(plate)
            canvas.tag_bind(tag, "<Enter>", self._lighter(plate, PLATE_LIT, "hand2"))
            canvas.tag_bind(tag, "<Leave>", self._lighter(plate, PLATE, ""))
            canvas.tag_bind(tag, "<Button-1>", self._pressed(action))
        return int(plate_width + pad)

    def _pressed(self, action: Callable[[], None]) -> Callable[[Any], None]:
        def handler(_event: Any) -> None:
            action()

        return handler

    def _lighter(self, plate: int, colour: str, cursor: str) -> Callable[[Any], None]:
        def handler(_event: Any) -> None:
            self.canvas.itemconfig(plate, fill=colour)
            self.canvas.config(cursor=cursor)

        return handler


__all__ = ["AMBER", "GREEN", "RED", "BadgeView", "mix", "shorten"]
