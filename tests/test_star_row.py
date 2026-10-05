"""Star row reader (M7): synthetic star rows (always run) and the user's captures (local fixtures only).

The synthetic stars are regular five-pointed stars drawn here in the proportions measured on the captures (pitch 0.4
name-line heights, star top 0.28 below the name box top, 1.3 pitches tall): plain ones uniform yellow, awakened ones
with a yellow-orange top, a magenta lower half and a dark centre. Negative controls (orange level text, gear badges)
must give no star count.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import cv2
import numpy as np
import pytest

from e7ac.vision.hero_screen import ScreenAnchors
from e7ac.vision.ocr import Box
from e7ac.vision.star_row import AWAKENED, PLAIN, UNKNOWN, StarRowReading, read_star_row
from tests.test_imprint_icon import (
    MASTER,
    ORANGE,
    SCALES,
    WHITE,
    Bgr,
    Drawing,
    Image,
    golden_params,
    golden_screen,
    hsv_bgr,
    scaled_box,
)

BACKGROUND: Final = hsv_bgr(175, 170, 30)
YELLOW: Final = hsv_bgr(21, 200, 240)
"""Plain star (measured centre hue 20-22)."""
AWAKENED_TOP: Final = hsv_bgr(18, 220, 245)
AWAKENED_LOWER: Final = hsv_bgr(170, 200, 230)
"""Magenta-pink lower half (measured hue 140-178 in the prototype's pink test)."""
DARK_CENTRE: Final = hsv_bgr(170, 120, 60)
PITCH: Final = 0.4
"""Star pitch in name-line heights (measured 0.33-0.48)."""
NAME: Final = (0.5, 1.0, 2.0)
"""Name box x0, y0, y1 in name-line heights."""


def star_points(cx: float, top: float, pitch: float) -> list[tuple[float, float]]:
    """A five-pointed star with its tip at (cx, top), 1.30 pitches tall (plain stars measured 1.25-1.32)."""
    outer = 0.72 * pitch
    inner = 0.48 * outer
    centre_y = top + outer
    points = []
    for k in range(10):
        angle = np.radians(-90 + 36 * k)
        radius = outer if k % 2 == 0 else inner
        points.append((cx + radius * np.cos(angle), centre_y + radius * np.sin(angle)))
    return points


def draw_star(d: Drawing, cx: float, top: float, kind: str, pitch: float = PITCH) -> None:
    """kind: 'p' plain, 'A' awakened, '?' a half-dark centre without pink (neither)."""
    points = star_points(cx, top, pitch)
    if kind in "p?":
        d.polygon(points, YELLOW)
        if kind == "?":
            cv2.circle(d.image, d.pt(cx, top + 0.72 * pitch), d.px(0.2 * pitch), hsv_bgr(21, 200, 185), -1)
        return
    d.polygon(points, AWAKENED_LOWER)
    upper = np.zeros(d.image.shape[:2], np.uint8)
    cv2.fillPoly(upper, [np.array([d.pt(x, y) for x, y in points], np.int32)], 255, cv2.LINE_AA)
    upper[round((top + 0.85 * pitch) * MASTER) :] = 0
    d.image[upper > 128] = AWAKENED_TOP
    cv2.circle(d.image, d.pt(cx, top + 0.72 * pitch), d.px(0.2 * pitch), DARK_CENTRE, -1, cv2.LINE_AA)


def name_line(d: Drawing, name: str) -> float:
    """Draw the white hero name; returns the name box's right end."""
    d.text(name, NAME[0], NAME[2] - 0.2, 2.8, WHITE)
    (width, _), _ = cv2.getTextSize(name, cv2.FONT_HERSHEY_DUPLEX, 2.8, 4)
    return NAME[0] + width / MASTER


def star_screen(kinds: str, scale: float, gaps: Sequence[float] | None = None) -> tuple[Image, ScreenAnchors]:
    """A hero name followed by one star per character of `kinds`; the anchors hold the OCR name box only."""
    d = Drawing(12.0, 3.0, BACKGROUND)
    right = name_line(d, "Haru")
    x, top = right + 0.3 + 0.45 * PITCH, NAME[1] + 0.28
    for i, kind in enumerate(kinds):
        draw_star(d, x, top, kind)
        x += PITCH * (gaps[i] if gaps else 1.0)
    image, unit = d.at_scale(scale)
    return image, ScreenAnchors(name=scaled_box(unit, NAME[0], NAME[1], right, NAME[2]))


def distractor_screen(scale: float, text: str | None, colour: Bgr = ORANGE) -> tuple[Image, ScreenAnchors]:
    """A 'name' followed by orange text (the level line) or, with `text` None, orange gear badges."""
    d = Drawing(12.0, 3.0, BACKGROUND)
    right = name_line(d, "Name")
    if text is not None:
        d.text(text, right + 0.3, NAME[2] - 0.2, 2.8, colour, 5)
    else:
        for i, badge in enumerate(("+15", "88", "+30")):
            x = right + 0.5 + 1.3 * i
            d.rounded_rect(x + 0.5, 1.5, 0.5, 0.3, 0.12, hsv_bgr(18, 230, 235))
            d.text(badge, x + 0.1, 1.65, 1.2, WHITE, 2)
    image, unit = d.at_scale(scale)
    return image, ScreenAnchors(name=scaled_box(unit, NAME[0], NAME[1], right, NAME[2]))


def per_star(kinds: str) -> tuple[str, ...]:
    return tuple({"A": AWAKENED, "p": PLAIN, "?": UNKNOWN}[k] for k in kinds)


def assert_consistent(reading: StarRowReading) -> None:
    """MECH-HERO-02 holds by construction: awakened never exceeds the star count."""
    if reading.stars is None:
        assert (reading.awakened, reading.per_star, reading.confidence) == (None, (), 0.0)
    else:
        assert len(reading.per_star) == reading.stars
        assert reading.awakened is None or reading.awakened <= reading.stars


# ------------------------------------------------------------------------------------------------ synthetic tests


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize(
    ("kinds", "cap", "awakened"),
    [("ppppp", 50, 0), ("AAAAAA", 60, 6), ("Apppp", 50, 1), ("AAAppp", 60, 3)],
)
def test_star_row(scale: float, kinds: str, cap: int, awakened: int) -> None:
    reading = read_star_row(*star_screen(kinds, scale), level_cap=cap)
    assert (reading.stars, reading.awakened, reading.per_star) == (len(kinds), awakened, per_star(kinds))
    assert (reading.confidence, reading.warnings) == (0.9, ())
    assert_consistent(reading)


@pytest.mark.parametrize("scale", SCALES)
def test_awakened_star_after_a_plain_one_is_a_warning(scale: float) -> None:
    """Awakened stars are assumed to fill from the left (one sample: Politis 1/5)."""
    reading = read_star_row(*star_screen("pAppp", scale), level_cap=50)
    assert (reading.stars, reading.awakened) == (5, 1)
    assert reading.confidence == 0.5
    assert reading.warnings == (
        "an awakened star follows a plain one (assumed impossible: awakening fills from the left)",
    )


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("cap", [50, 55, 70])
def test_level_cap_cross_check(scale: float, cap: int) -> None:
    """MECH-HERO-01 (community): cap = stars x 10. A mismatch is a warning, the count is never corrected."""
    reading = read_star_row(*star_screen("AAAAAA", scale), level_cap=cap)
    assert (reading.stars, reading.awakened, reading.confidence) == (6, 6, 0.5)
    assert reading.warnings == (f"6 star(s) but level cap {cap} (MECH-HERO-01: cap = stars x 10)",)


def test_without_level_cap_there_is_no_cross_check() -> None:
    reading = read_star_row(*star_screen("ppppp", 1.0), level_cap=None)
    assert (reading.stars, reading.confidence, reading.warnings) == (5, 0.9, ())


@pytest.mark.parametrize("scale", [1.0, 1.28])  # at 0.64 the estimated pitch makes the tip too wide: abstains
def test_single_star_has_low_confidence(scale: float) -> None:
    reading = read_star_row(*star_screen("p", scale), level_cap=10)
    assert (reading.stars, reading.awakened, reading.confidence) == (1, 0, 0.4)
    assert reading.warnings == ("single star: pitch estimated from its height",)


@pytest.mark.parametrize("scale", SCALES)
def test_irregular_spacing_gives_no_count(scale: float) -> None:
    reading = read_star_row(*star_screen("ppppp", scale, gaps=[1, 1, 1.5, 1, 1]), level_cap=50)
    assert reading.stars is None
    assert len(reading.warnings) == 1 and reading.warnings[0].startswith("irregular star spacing")
    assert_consistent(reading)


@pytest.mark.parametrize("scale", SCALES)
def test_unclear_star_leaves_the_awakening_unknown(scale: float) -> None:
    reading = read_star_row(*star_screen("AA?ppp", scale), level_cap=60)
    assert (reading.stars, reading.awakened, reading.per_star) == (6, None, per_star("AA?ppp"))
    assert reading.confidence == 0.5
    assert reading.warnings == ("some stars are neither clearly awakened nor plain: awakening not read",)


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("text", ["Lv. Max/60", "Lv.5/50", "88", "+15", None], ids=str)
def test_negative_controls_give_no_stars(scale: float, text: str | None) -> None:
    """The orange level text and the gear badges are in the star hue range: the shape checks must reject them."""
    reading = read_star_row(*distractor_screen(scale, text), level_cap=60)
    assert reading.stars is None and reading.warnings
    assert_consistent(reading)


def test_without_name_anchor_there_is_no_row() -> None:
    image, _ = star_screen("ppppp", 1.0)
    reading = read_star_row(image, ScreenAnchors(), level_cap=50)
    assert reading == StarRowReading(None, None, (), 0.0, ("no hero name line: the star row has no anchor",))
    outside = read_star_row(image, ScreenAnchors(name=Box(5000, 5000, 5100, 5040)), level_cap=50)
    assert (outside.stars, outside.warnings) == (None, ("the hero name line is outside the image",))


def test_name_without_stars_gives_no_row() -> None:
    d = Drawing(12.0, 3.0, BACKGROUND)
    right = name_line(d, "Haru")
    image, unit = d.at_scale(1.0)
    reading = read_star_row(image, ScreenAnchors(name=scaled_box(unit, NAME[0], NAME[1], right, NAME[2])), 60)
    assert (reading.stars, reading.warnings) == (None, ("no yellow star parts next to the name",))


# ------------------------------------------------------------------------------------------------ user captures

GOLDEN: Final = {
    # truth by eye (scratch truth.json, 2026-10-04): A = awakened (multicoloured, dark centre), p = plain
    "heroinfo_haru": "AAAAAA",
    "heroinfo_lots": "AAAAAA",
    "heroinfo_ainz": "AAAAAA",
    "heroinfo_straze": "AAAAAA",
    "heroinfo_politis": "Apppp",  # the only partial awakening seen (1 of 5)
    "heroinfo_charles": "ppppp",
    "equip_renoa": "AAAAAA",
    "equip_haru": "AAAAAA",
    "equip_straze": "AAAAAA",
}


@pytest.mark.parametrize(("name", "scale"), golden_params(GOLDEN))
def test_real_capture(name: str, scale: float) -> None:
    image, screen = golden_screen(name, scale)
    kinds = GOLDEN[name]
    reading = read_star_row(image, screen.anchors, screen.level_cap)
    assert (reading.stars, reading.awakened) == (len(kinds), kinds.count("A"))
    assert reading.per_star == per_star(kinds)
    assert (reading.confidence, reading.warnings) == (0.9, ())
    assert screen.level_cap == 10 * len(kinds)  # MECH-HERO-01 holds on every capture


@pytest.mark.parametrize(("name", "scale"), golden_params(GOLDEN))
def test_real_capture_level_and_cp_lines_have_no_stars(name: str, scale: float) -> None:
    """Negative controls: the orange "Lv. Max/60" line and the CP line (gold set icons next to it)."""
    image, screen = golden_screen(name, scale)
    for box in (screen.anchors.level, screen.anchors.cp):
        assert box is not None
        reading = read_star_row(image, ScreenAnchors(name=box), screen.level_cap)
        assert reading.stars is None, (box, reading.warnings)
