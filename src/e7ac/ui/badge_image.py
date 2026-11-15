"""The badge as a picture, drawn the way the badge over the game draws itself (SPEC D122).

"Save picture" shows the game with the badge on it. The badge is hidden from screen capture so that the readers never
see it, and where the capture still does not show it, the picture is made here: the same panel, edge, stripe, light,
headline, line and buttons as `ui.badge_view`, in the same colours and proportions, drawn with Pillow (already there:
the OCR needs it) instead of a Tk canvas, so it needs no window and no Windows. Pure drawing."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from e7ac.ui import badge_view as view
from e7ac.ui.watch_signal import DEFAULT_SIZE, HEIGHT, LIGHT, SIZES, WIDTH, WITH_BUTTONS, metrics

type BgrImage = npt.NDArray[np.uint8]

REFERENCE_HEIGHT: Final = 740
"""The game picture's height at which the sizes are as in `watch_signal.SIZES` (a taller picture scales them up)."""
TOP_MARGIN: Final = 8
"""Pixels between the top of the game and the badge."""
_FONTS: Final = {"normal": ("segoeui.ttf", "DejaVuSans.ttf"), "bold": ("segoeuib.ttf", "DejaVuSans-Bold.ttf")}


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _font(pixels: int, weight: str) -> Any:
    from PIL import ImageFont

    for name in _FONTS[weight]:
        try:
            return ImageFont.truetype(name, max(6, pixels))
        except OSError:
            continue
    return ImageFont.load_default(max(6, pixels))


def _fitted(text: str, pixels: int, weight: str, room: int) -> tuple[str, Any]:
    """As `BadgeView.fitted`: the largest font down to `SHRINK` of the size that holds the text, else the text cut."""
    for size in range(pixels, max(6, round(pixels * view.SHRINK)) - 1, -1):
        font = _font(size, weight)
        if font.getlength(text) <= room:
            return text, font
    font = _font(max(6, round(pixels * view.SHRINK)), weight)
    return view.shorten(text, lambda s: bool(font.getlength(s) <= room)), font


def render_badge(
    top: str,
    bottom: str,
    colour: str,
    buttons: Sequence[str] = (),
    size: str = DEFAULT_SIZE,
    scale: float = 1.0,
) -> BgrImage:
    """The badge saying `top` (in `colour`) and `bottom`, with plates for `buttons`, at this screen scale (the DPI
    scale of the screen it was on; `scale_for` finds it from a badge's size or a picture's height)."""
    from PIL import Image, ImageDraw

    width, height, title, line, plate_font = metrics(size, scale, bool(buttons))
    unit = height / HEIGHT
    light = view._LIGHT.get(colour, colour)
    rows = np.array([[int(c) for c in _rgb(view.mix(view.PANEL_TOP, view.PANEL_BOTTOM, y / max(1, height - 1)))]
                     for y in range(height)], np.uint8)  # fmt: skip
    canvas = Image.fromarray(np.repeat(rows[:, None, :], width, axis=1), "RGB")
    draw = ImageDraw.Draw(canvas)
    draw.line((round(3 * unit), 1, width - round(3 * unit), 1), fill=_rgb(view.SHINE))
    draw.rectangle((0, 0, width - 1, height - 1), outline=_rgb(view.EDGE))
    draw.rectangle((1, 1, width - 2, height - 2), outline=_rgb(view.mix(view.EDGE, view.PANEL_BOTTOM, 0.55)))
    stripe = max(3, round(5 * unit))
    draw.rectangle((0, 0, stripe, height), fill=_rgb(light))
    draw.line((stripe, 0, stripe, height), fill=_rgb(view.mix(light, view.PANEL_BOTTOM, 0.5)))
    plates = _plates(draw, buttons, width, height, unit, plate_font)
    left = stripe + round(30 * unit)
    room = width - left - plates - round(10 * unit)
    radius, centre, x = round(5.5 * unit), round(height * 0.33), stripe + round(11 * unit)
    glow = _rgb(view.mix(light, view.PANEL_BOTTOM, 0.65))
    draw.ellipse((x - radius - 2, centre - radius - 2, x + radius + 2, centre + radius + 2), fill=glow)
    draw.ellipse((x - radius, centre - radius, x + radius, centre + radius), fill=_rgb(light), outline=_rgb(
        view.mix(light, view.TEXT, 0.5)))  # fmt: skip
    head, head_font = _fitted(top, title, "bold", room)
    draw.text((left, centre), head, font=head_font, fill=_rgb(view._HEADLINE.get(colour, colour)), anchor="lm")
    text, text_font = _fitted(bottom, line, "normal", room)
    draw.text(
        (left, round(height * 0.70)), text, font=text_font, fill=_rgb(view._LINE.get(LIGHT, view.MUTED)), anchor="lm"
    )
    return np.asarray(canvas, np.uint8)[:, :, ::-1].copy()


def _plates(draw: Any, buttons: Sequence[str], width: int, height: int, unit: float, pixels: int) -> int:
    """The buttons as plates stacked at the right (as `BadgeView._draw_plates`); the width they take."""
    if not buttons:
        return 0
    font = _font(pixels, "bold")
    pad = round(7 * unit)
    plate_width = round(max(font.getlength(label) for label in buttons)) + round(22 * unit)
    gap = max(2, round(3 * unit))
    each = (height - 2 * pad - gap * (len(buttons) - 1)) / len(buttons)
    x1 = width - pad
    x0 = x1 - plate_width
    for index, label in enumerate(buttons):
        y0 = pad + index * (each + gap)
        draw.rectangle((x0, y0, x1, y0 + each), fill=_rgb(view.PLATE), outline=_rgb(view.EDGE_LIT))
        draw.text(((x0 + x1) / 2, y0 + each / 2), label, font=font, fill=_rgb(view.TEXT), anchor="mm")
    return int(plate_width + pad)


def scale_for(picture_height: int, size: str = DEFAULT_SIZE, badge_width: int | None = None) -> float:
    """The scale a badge is drawn at: the one that gives the width the real badge has (`badge_width`, with buttons),
    else the one that makes it as large on this picture as it would be on a 740 px high one."""
    if badge_width is not None:
        return badge_width / (WIDTH * SIZES.get(size, SIZES[DEFAULT_SIZE]) * WITH_BUTTONS)
    return max(1.0, picture_height / REFERENCE_HEIGHT)


def put_badge(game: BgrImage, badge: BgrImage, at: tuple[int, int] | None = None) -> BgrImage:
    """A copy of the game picture with the badge where it stands over the game: `at` (left, top) when known, else the
    top centre."""
    out = game.copy()
    height, width = out.shape[:2]
    top = TOP_MARGIN if at is None else max(0, at[1])
    left = max(0, (width - badge.shape[1]) // 2) if at is None else max(0, at[0])
    part = badge[: max(0, height - top), : max(0, width - left)]
    out[top : top + part.shape[0], left : left + part.shape[1]] = part
    return np.asarray(out, np.uint8)
