"""Is the badge in the picture of the game? (SPEC D105)

The badge sits over the game and is hidden from the screen capture (`SetWindowDisplayAffinity`). On the user's PC its
text ("Read: Notos", the skill's name under it) nevertheless reached the reader once, and a reading of the badge
replaced a real skill of the same name. Whether the hiding holds cannot be known from here, so the watches check the
picture itself: where the badge was placed, do the pixels have its deep-navy gradient and its gold edge? If they do, the
badge is in the picture and its rectangle is blanked before anything is read. Pure image maths (no Windows, no Tk)."""

from __future__ import annotations

from typing import Final

import numpy as np
import numpy.typing as npt

from e7ac.ui.badge_view import EDGE, PANEL_BOTTOM, PANEL_TOP
from e7ac.vision.window import Rect

type BgrImage = npt.NDArray[np.uint8]

MIN_WIDTH: Final = 40
MIN_HEIGHT: Final = 12
TOLERANCE: Final = 34
"""Per channel: the badge is 92% opaque, so up to 8% of the game under it shows through its colours."""
PANEL_SHARE: Final = 0.4
"""Share of the rectangle's pixels that must have the panel's colour of their row (the rest: text and plates)."""
EDGE_TOLERANCE: Final = 40
EDGE_SHARE: Final = 0.5
"""Share of one of the outer rows (a few, in case the picture is a pixel off) that must be the gold edge."""
EDGE_ROWS: Final = 3


def _bgr(colour: str) -> npt.NDArray[np.int16]:
    return np.array([int(colour[i : i + 2], 16) for i in (5, 3, 1)], np.int16)


def badge_visible(image: BgrImage, rect: Rect) -> bool:
    """Whether the badge, placed at `rect` (in the picture's own pixels), is drawn in the picture."""
    height, width = image.shape[:2]
    left, top = max(0, rect.left), max(0, rect.top)
    right, bottom = min(width, rect.left + rect.width), min(height, rect.top + rect.height)
    if right - left < MIN_WIDTH or bottom - top < MIN_HEIGHT:
        return False
    crop = image[top:bottom, left:right].astype(np.int16)
    row = np.arange(top, bottom) - rect.top  # the row of the badge each row of the crop is
    share = row / max(1, rect.height - 1)
    first, last = _bgr(PANEL_TOP), _bgr(PANEL_BOTTOM)
    expected = first + (last - first) * share[:, None]
    panel = (np.abs(crop - expected[:, None, :]) <= TOLERANCE).all(axis=2)
    if float(panel.mean()) < PANEL_SHARE:
        return False
    gold = (np.abs(crop - _bgr(EDGE)) <= EDGE_TOLERANCE).all(axis=2)
    outer = np.flatnonzero((row < EDGE_ROWS) | (row >= rect.height - EDGE_ROWS))
    return any(float(gold[i].mean()) >= EDGE_SHARE for i in outer)


def hide_badge(image: BgrImage, area: Rect, region: Rect | None) -> tuple[BgrImage, bool]:
    """The picture without the badge when it shows (its rectangle blanked), and whether it did. `area` is where the
    picture sits on the screen, `region` where the badge is on the screen."""
    if region is None:
        return image, False
    rect = Rect(region.left - area.left, region.top - area.top, region.width, region.height)
    if not badge_visible(image, rect):
        return image, False
    out = image.copy()
    out[max(0, rect.top) : rect.top + rect.height, max(0, rect.left) : rect.left + rect.width] = 0
    return out, True
