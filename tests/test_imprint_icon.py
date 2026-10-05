"""Imprint icon reader (M7): synthetic icons (always run) and the user's captures (local fixtures only).

The synthetic icons are simple shapes drawn here in the proportions measured on the captures (four rounded squares
in a diamond, a crosshair, grey squares with a padlock, grade letters with a dark outline), not the game's artwork:
they exercise the mechanics (window from the anchors, mask, geometry, mode, lit squares, letter count, the grade
table, the Locked structure) at three UI scales, plus negative controls that must stay unknown.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from functools import cache
from typing import Final

import cv2
import numpy as np
import numpy.typing as npt
import pytest

from e7ac.domain.codes import DataStatus
from e7ac.domain.roster import ImprintGrade, ImprintMode
from e7ac.vision.hero_screen import HeroScreenReading, ScreenAnchors, parse_hero_screen
from e7ac.vision.imprint_icon import (
    GRADE_RULES,
    SQUARES,
    ImprintIconReading,
    colour_family,
    locked_mismatch,
    read_icon_window,
    read_imprint_icon,
)
from e7ac.vision.ocr import Box, RapidOcrReader, TextLine
from tests.markers import FIXTURES_DIR

type Image = npt.NDArray[np.uint8]
type Bgr = tuple[int, int, int]

SCALES: Final = (0.64, 1.0, 1.28)
MASTER: Final = 80
"""Pixels per text line in the master drawings, resized (INTER_AREA) to each UI scale like a smaller window."""
BASE_LINE: Final = 32
"""Text line height in pixels at UI scale 1.0 (the user's 2000-px copies: imprint text lines 34-38 px)."""
BLUE: Final = 107
RED: Final = 0
ALL_LIT: Final = frozenset(SQUARES)


def hsv_bgr(hue: int, saturation: int, value: int) -> Bgr:
    pixel = cv2.cvtColor(np.array([[[hue, saturation, value]]], np.uint8), cv2.COLOR_HSV2BGR)[0, 0]
    return int(pixel[0]), int(pixel[1]), int(pixel[2])


BACKGROUND: Final = hsv_bgr(175, 170, 22)
"""Dark crimson behind the panel (measured median H 174-177, S 161-170, V 13-36)."""
OUTLINE: Final = hsv_bgr(175, 120, 10)
ORANGE: Final = hsv_bgr(15, 220, 240)
"""The level text and the gear badges are orange (inside the star yellow hue range)."""
WHITE: Final = hsv_bgr(0, 0, 245)


class Drawing:
    """A master canvas in text-line units, resized to a UI scale like the real captures."""

    def __init__(self, width: float, height: float, background: Bgr = BACKGROUND) -> None:
        self.image: Image = np.full((round(height * MASTER), round(width * MASTER), 3), background, np.uint8)

    @staticmethod
    def pt(x: float, y: float) -> tuple[int, int]:
        return round(x * MASTER), round(y * MASTER)

    @staticmethod
    def px(length: float) -> int:
        return max(1, round(length * MASTER))

    def rounded_rect(self, cx: float, cy: float, half_w: float, half_h: float, radius: float, colour: Bgr) -> None:
        x0, x1, y0, y1 = cx - half_w, cx + half_w, cy - half_h, cy + half_h
        cv2.rectangle(self.image, self.pt(x0 + radius, y0), self.pt(x1 - radius, y1), colour, -1, cv2.LINE_AA)
        cv2.rectangle(self.image, self.pt(x0, y0 + radius), self.pt(x1, y1 - radius), colour, -1, cv2.LINE_AA)
        for qx, qy in ((x0 + radius, y0 + radius), (x1 - radius, y0 + radius), (x0 + radius, y1 - radius)):
            cv2.circle(self.image, self.pt(qx, qy), self.px(radius), colour, -1, cv2.LINE_AA)
        cv2.circle(self.image, self.pt(x1 - radius, y1 - radius), self.px(radius), colour, -1, cv2.LINE_AA)

    def polygon(self, points: Sequence[tuple[float, float]], colour: Bgr) -> None:
        pts = np.array([self.pt(x, y) for x, y in points], np.int32)
        cv2.fillPoly(self.image, [pts], colour, cv2.LINE_AA)

    def diamond(self, cx: float, cy: float, half_w: float, half_h: float, colour: Bgr) -> None:
        self.polygon([(cx, cy - half_h), (cx + half_w, cy), (cx, cy + half_h), (cx - half_w, cy)], colour)

    def text(self, text: str, x: float, baseline: float, scale: float, colour: Bgr, thickness: int = 4) -> None:
        cv2.putText(
            self.image, text, self.pt(x, baseline), cv2.FONT_HERSHEY_DUPLEX, scale, colour, thickness, cv2.LINE_AA
        )

    def at_scale(self, scale: float) -> tuple[Image, float]:
        """The image at a UI scale and its pixels per text line."""
        factor = scale * BASE_LINE / MASTER
        small = np.asarray(
            cv2.resize(self.image, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA), dtype=np.uint8
        )
        return small, MASTER * factor


def scaled_box(unit: float, x0: float, y0: float, x1: float, y1: float) -> Box:
    return Box(x0 * unit, y0 * unit, x1 * unit, y1 * unit)


# ------------------------------------------------------------------------------------------------ synthetic icons


def grade_letters(d: Drawing, text: str, x: float, baseline: float, height: float, hue: int) -> None:
    """Grade letters in the icon colour with a dark outline (FONT_HERSHEY_DUPLEX capitals: ~22 px at scale 1)."""
    scale = height * MASTER / 22.0
    thickness = max(2, int(scale * 2.2))
    d.text(text, x, baseline, scale, OUTLINE, thickness + int(scale * 2.5))
    d.text(text, x, baseline, scale, hsv_bgr(hue, 200, 255), thickness)


def team_icon(
    d: Drawing, cx: float, cy: float, hue: int, lit: Iterable[str], grade: str, letter_hue: int | None = None
) -> None:
    """Four rounded squares in a diamond (Rx/Ry = 1.25), unlit ones at 30 % brightness, each with a dark hole."""
    lit = frozenset(lit)
    centres = {"top": (0.0, -0.62), "left": (-0.77, 0.0), "right": (0.77, 0.0), "bottom": (0.0, 0.62)}
    for square in sorted(centres, key=lambda s: s in lit):  # lit squares drawn last
        dx, dy = centres[square]
        d.rounded_rect(cx + dx, cy + dy, 0.48, 0.38, 0.14, hsv_bgr(hue, 195, 255 if square in lit else 77))
        d.diamond(cx + dx, cy + dy, 0.2, 0.15, BACKGROUND)
    if grade:
        grade_letters(d, grade, cx + 0.05, cy + 1.4, 0.9, hue if letter_hue is None else letter_hue)


def self_icon(d: Drawing, cx: float, cy: float, hue: int, grade: str) -> None:
    """Crosshair: a dim ring, four lit pointers, a lit centre cross."""
    dim, lit = hsv_bgr(hue, 195, 85), hsv_bgr(hue, 195, 255)
    cv2.circle(d.image, d.pt(cx, cy), d.px(0.70), dim, d.px(0.24), cv2.LINE_AA)
    for angle in (0.0, 90.0, 180.0, 270.0):
        c, s = np.cos(np.radians(angle)), np.sin(np.radians(angle))
        tip, base = (cx + 0.57 * c, cy + 0.57 * s), (cx + c, cy + s)
        d.polygon([tip, (base[0] - 0.22 * s, base[1] + 0.22 * c), (base[0] + 0.22 * s, base[1] - 0.22 * c)], lit)
    cv2.circle(d.image, d.pt(cx, cy), d.px(0.22), lit, -1, cv2.LINE_AA)
    cv2.line(d.image, d.pt(cx - 0.45, cy), d.pt(cx + 0.45, cy), lit, d.px(0.12), cv2.LINE_AA)
    cv2.line(d.image, d.pt(cx, cy - 0.45), d.pt(cx, cy + 0.45), lit, d.px(0.12), cv2.LINE_AA)
    if grade:
        grade_letters(d, grade, cx + (0.55 if len(grade) == 1 else -0.1), cy + 1.4, 0.9, hue)


def locked_icon(d: Drawing, cx: float, cy: float) -> None:
    """Translucent grey squares (left and right lighter, as on the one sample) and a white padlock bottom right."""
    for dx, dy, value in ((0.0, -0.66, 30), (-0.88, 0.0, 100), (0.88, 0.0, 100), (0.0, 0.66, 30)):
        d.rounded_rect(cx + dx, cy + dy, 0.45, 0.36, 0.13, hsv_bgr(0, 30, value))
        d.diamond(cx + dx, cy + dy, 0.2, 0.15, BACKGROUND)
    px, py = cx + 0.75, cy + 0.75
    cv2.ellipse(d.image, d.pt(px, py - 0.1), (d.px(0.2), d.px(0.24)), 0, 180, 360, WHITE, d.px(0.08), cv2.LINE_AA)
    d.rounded_rect(px, py + 0.13, 0.31, 0.25, 0.05, WHITE)
    cv2.circle(d.image, d.pt(px, py + 0.13), d.px(0.1), BACKGROUND, -1, cv2.LINE_AA)


LEVEL: Final = (2.0, 1.0, 6.0, 2.45)
TEXT: Final = (5.6, 3.8, 10.5, 4.8)
CP: Final = (2.0, 6.55, 5.0, 7.55)
"""Panel layout in text lines: level line (1.45 lines tall), imprint text, CP; the icon window lies between them."""


def icon_screen(
    kind: str,
    scale: float,
    hue: int = BLUE,
    lit: Iterable[str] = ALL_LIT,
    grade: str = "B",
    text_colour: Bgr | None = None,
    letter_hue: int | None = None,
    extra: Callable[[Drawing, float, float], None] | None = None,
) -> tuple[Image, ScreenAnchors]:
    """A hero panel with the level line, an imprint icon of `kind` (team / self / locked / none), the imprint text and
    the CP; returns the image at `scale` and the anchors `parse_hero_screen` would give."""
    d = Drawing(12.0, 8.0)
    rx = 1.0 if kind == "self" else 1.25
    cx, cy = 2.13 + rx, 3.3 + 1.0
    if kind == "team":
        team_icon(d, cx, cy, hue, lit, grade, letter_hue)
    elif kind == "self":
        self_icon(d, cx, cy, hue, grade)
    elif kind == "locked":
        locked_icon(d, cx, cy)
    if extra is not None:
        extra(d, cx, cy)
    d.text("Lv. Max/60", LEVEL[0], LEVEL[3] - 0.15, 2.4, ORANGE)
    default_text = hsv_bgr(0, 0, 200) if kind == "locked" else hsv_bgr(hue, 200, 255)
    d.text("Locked" if kind == "locked" else "Health + 4%", TEXT[0], TEXT[3] - 0.15, 2.6, text_colour or default_text)
    d.text("124,427", CP[0], CP[3] - 0.15, 2.6, hsv_bgr(0, 0, 230))
    image, unit = d.at_scale(scale)
    return image, ScreenAnchors(
        level=scaled_box(unit, *LEVEL), imprint=(scaled_box(unit, *TEXT),), cp=scaled_box(unit, *CP)
    )


def lit_states(lit: Iterable[str]) -> dict[str, str]:
    lit = frozenset(lit)
    return {square: "lit" if square in lit else "dark" for square in SQUARES}


# ------------------------------------------------------------------------------------------------ synthetic tests


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize(
    ("hue", "grade", "colour", "letters", "expected"),
    [(BLUE, "B", "blue", 1, ImprintGrade.B), (RED, "SSS", "red", 3, ImprintGrade.SSS)],
)
def test_team_icon_with_all_squares_lit(
    scale: float, hue: int, grade: str, colour: str, letters: int, expected: ImprintGrade
) -> None:
    reading = read_imprint_icon(*icon_screen("team", scale, hue=hue, grade=grade))
    assert (reading.mode, reading.locked, reading.colour) == (ImprintMode.TEAM, False, colour)
    assert (reading.letters, reading.grade, reading.grade_confidence) == (letters, expected, 0.9)
    assert reading.lit == lit_states(ALL_LIT)
    assert reading.mode_confidence >= 0.8
    assert reading.warnings == ()
    assert 1.14 <= reading.measurements["aspect"] <= 1.42


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("lit", [("left", "bottom"), ("right", "bottom"), ("top",)])
def test_team_icon_tells_lit_from_dark_squares(scale: float, lit: tuple[str, ...]) -> None:
    reading = read_imprint_icon(*icon_screen("team", scale, hue=RED, grade="SSS", lit=lit))
    assert reading.mode is ImprintMode.TEAM
    assert reading.lit == lit_states(lit)
    assert reading.grade is ImprintGrade.SSS
    assert reading.warnings == ()


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize(("hue", "grade", "expected"), [(BLUE, "B", ImprintGrade.B), (RED, "SSS", ImprintGrade.SSS)])
def test_self_icon(scale: float, hue: int, grade: str, expected: ImprintGrade) -> None:
    reading = read_imprint_icon(*icon_screen("self", scale, hue=hue, grade=grade))
    assert (reading.mode, reading.lit, reading.grade) == (ImprintMode.SELF, None, expected)
    assert reading.measurements["centre"] == 1.0
    assert 0.88 <= reading.measurements["aspect"] <= 1.12
    assert reading.warnings == ()


@pytest.mark.parametrize("scale", SCALES)
def test_locked_icon_is_only_a_consistency_check(scale: float) -> None:
    reading = read_imprint_icon(*icon_screen("locked", scale))
    assert (reading.mode, reading.locked, reading.colour) == (None, True, "grey")
    assert (reading.letters, reading.grade, reading.lit, reading.mode_confidence) == (None, None, None, 0.0)
    assert reading.warnings == ()
    assert locked_mismatch(reading, text_locked=True) is None
    assert locked_mismatch(reading, text_locked=False) == "the icon looks Locked but the text shows an imprint"


def test_locked_mismatch_messages() -> None:
    image, anchors = icon_screen("team", 1.0)
    team = read_imprint_icon(image, anchors)
    assert locked_mismatch(team, text_locked=False) is None
    assert locked_mismatch(team, text_locked=True) == "the text says 'Locked' but the icon reads as a team imprint"
    nothing = read_imprint_icon(*icon_screen("none", 1.0))
    assert (nothing.mode, nothing.locked) == (None, False)
    assert locked_mismatch(nothing, text_locked=True) == "the text says 'Locked' but the Locked icon was not found"
    assert locked_mismatch(nothing, text_locked=False) is None


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize(
    ("kind", "hue", "grade", "letters"),
    [("team", BLUE, "SSS", 3), ("team", RED, "B", 1), ("team", 60, "B", 1), ("self", BLUE, "SSS", 3)],
)
def test_unseen_colour_and_letter_pairs_give_no_grade(
    scale: float, kind: str, hue: int, grade: str, letters: int
) -> None:
    """Only (blue, 1) = B and (red, 3) = SSS were seen: anything else is None with the measurements in a warning."""
    reading = read_imprint_icon(*icon_screen(kind, scale, hue=hue, grade=grade))
    assert reading.mode is not None and reading.letters == letters
    assert (reading.grade, reading.grade_confidence) == (None, 0.0)
    assert len(reading.warnings) == 1 and "was never seen" in reading.warnings[0]
    assert f"{letters} letter(s)" in reading.warnings[0] and "Ry" in reading.warnings[0]
    if hue == 60:
        assert reading.colour.startswith("other(h=")


@pytest.mark.parametrize("scale", SCALES)
def test_letters_of_another_colour_than_the_icon_are_not_counted(scale: float) -> None:
    """The letter mask keeps only the icon hue: red letters on a blue icon are not grade letters of that icon."""
    reading = read_imprint_icon(*icon_screen("team", scale, hue=BLUE, grade="B", letter_hue=RED))
    assert (reading.mode, reading.letters, reading.grade) == (ImprintMode.TEAM, 0, None)
    assert reading.warnings == ("no grade letters below the icon",)


def _red_bar_under_letters(d: Drawing, cx: float, cy: float) -> None:
    d.rounded_rect(cx + 0.9, cy + 1.6, 1.3, 0.12, 0.05, hsv_bgr(RED, 220, 255))


@pytest.mark.parametrize("scale", SCALES)
def test_letter_band_of_another_colour_gives_no_grade(scale: float) -> None:
    """Letters are counted in the icon hue, but their band's own colour must agree with the icon colour."""
    image, anchors = icon_screen("team", scale, hue=BLUE, grade="B", extra=_red_bar_under_letters)
    reading = read_imprint_icon(image, anchors)
    assert (reading.mode, reading.letters, reading.grade) == (ImprintMode.TEAM, 1, None)
    assert reading.warnings == ("grade letters look red but the icon is blue: grade not read",)


@pytest.mark.parametrize("scale", SCALES)
def test_imprint_text_colour_is_cross_checked(scale: float) -> None:
    reading = read_imprint_icon(*icon_screen("team", scale, hue=BLUE, text_colour=hsv_bgr(RED, 200, 255)))
    assert reading.grade is ImprintGrade.B
    assert reading.grade_confidence == 0.45
    assert reading.warnings == ("the imprint text looks red but the icon blue",)


def test_no_grade_letters_is_reported() -> None:
    reading = read_imprint_icon(*icon_screen("team", 1.0, grade=""))
    assert (reading.mode, reading.letters, reading.grade) == (ImprintMode.TEAM, 0, None)
    assert reading.warnings == ("no grade letters below the icon",)


def test_without_imprint_text_there_is_no_window() -> None:
    image, _ = icon_screen("team", 1.0)
    reading = read_imprint_icon(image, ScreenAnchors())
    assert (reading.mode, reading.colour, reading.grade) == (None, "unknown", None)
    assert reading.warnings == ("no imprint icon window: the imprint text (or the level line) was not found",)


@pytest.mark.parametrize("scale", SCALES)
def test_window_without_level_or_cp_line(scale: float) -> None:
    """Zoomed crops have no level line (band around the text block); a missing CP line ends the window lower."""
    image, anchors = icon_screen("team", scale, hue=RED, grade="SSS")
    for partial in (
        ScreenAnchors(imprint=anchors.imprint),
        ScreenAnchors(level=anchors.level, imprint=anchors.imprint),
    ):
        reading = read_imprint_icon(image, partial)
        assert (reading.mode, reading.grade, reading.warnings) == (ImprintMode.TEAM, ImprintGrade.SSS, ())


def _distractor_window(draw: Callable[[Drawing], None], scale: float) -> tuple[Image, float]:
    """An icon-window-sized image (4.4 x 4.1 text lines) holding only a distractor."""
    d = Drawing(4.4, 4.1)
    draw(d)
    return d.at_scale(scale)


def _level_text(d: Drawing) -> None:
    d.text("Lv. Max/60", 0.1, 2.3, 1.5, ORANGE)


def _red_imprint_text(d: Drawing) -> None:
    d.text("Health + 4%", 0.1, 2.3, 1.2, hsv_bgr(RED, 200, 255))


def _orange_badges(d: Drawing) -> None:
    for i in range(3):
        d.rounded_rect(0.7 + 1.4 * i, 2.0, 0.5, 0.3, 0.12, ORANGE)
        d.text("+15", 0.35 + 1.4 * i, 2.15, 1.0, WHITE, 2)


def _orange_disk(d: Drawing) -> None:
    """A filled disk like the orange one in a hero's art that fooled the prototype once."""
    cv2.circle(d.image, d.pt(2.2, 2.0), d.px(1.0), ORANGE, -1, cv2.LINE_AA)


def _grey_squares_without_padlock(d: Drawing) -> None:
    for i in range(2):
        d.rounded_rect(1.0 + 1.6 * i, 2.0, 0.45, 0.36, 0.13, hsv_bgr(0, 30, 100))
        d.diamond(1.0 + 1.6 * i, 2.0, 0.2, 0.15, BACKGROUND)


def _nothing(d: Drawing) -> None:
    del d


DISTRACTORS: Final[dict[str, Callable[[Drawing], None]]] = {
    "level text": _level_text,
    "red imprint text": _red_imprint_text,
    "orange badges": _orange_badges,
    "orange disk": _orange_disk,
    "grey squares without padlock": _grey_squares_without_padlock,
    "empty": _nothing,
}


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("name", sorted(DISTRACTORS))
def test_negative_controls_stay_unknown(scale: float, name: str) -> None:
    window, unit = _distractor_window(DISTRACTORS[name], scale)
    reading = read_icon_window(window, unit)
    assert (reading.mode, reading.locked, reading.grade, reading.letters) == (None, False, None, None)
    assert reading.mode_confidence == 0.0 and reading.warnings


def test_colour_family() -> None:
    assert [colour_family(h) for h in (0, 10, 175, 95, 107, 115)] == ["red", "red", "red", "blue", "blue", "blue"]
    assert [colour_family(h) for h in (11, 60, 94, 116, 169)] == [
        "other(h=11)",
        "other(h=60)",
        "other(h=94)",
        "other(h=116)",
        "other(h=169)",
    ]


def test_grade_table_holds_only_observed_pairs_with_provenance() -> None:
    assert {key: rule.grade for key, rule in GRADE_RULES.items()} == {
        ("blue", 1): ImprintGrade.B,
        ("red", 3): ImprintGrade.SSS,
    }
    assert {(rule.source, rule.status) for rule in GRADE_RULES.values()} == {
        ("user captures 2026-10-04", DataStatus.VERIFIED)
    }


def test_reading_is_immutable() -> None:
    reading = read_imprint_icon(*icon_screen("team", 1.0))
    assert isinstance(reading, ImprintIconReading)
    with pytest.raises(TypeError):
        reading.measurements["aspect"] = 2.0  # type: ignore[index]
    with pytest.raises(TypeError):
        reading.lit["top"] = "dark"  # type: ignore[index]


# ------------------------------------------------------------------------------------------------ user captures


@cache
def _reader() -> RapidOcrReader:
    return RapidOcrReader()  # the model loads on first use (~1 s)


@cache
def capture(path: str, scale: float) -> tuple[Image, tuple[TextLine, ...]]:
    """A local capture (relative to fixtures/) at a UI scale with its OCR lines, read once per test session (shared
    with test_star_row)."""
    from e7ac.vision.image import load_image

    image: Image = load_image(FIXTURES_DIR / path)
    if scale != 1.0:
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        image = np.asarray(cv2.resize(image, None, fx=scale, fy=scale, interpolation=interpolation), dtype=np.uint8)
    return image, tuple(_reader().read(image))


@cache
def golden_screen(name: str, scale: float) -> tuple[Image, HeroScreenReading]:
    """A capture of fixtures/screenshots/<name>.webp at a UI scale, parsed by `parse_hero_screen`."""
    image, lines = capture(f"screenshots/{name}.webp", scale)
    return image, parse_hero_screen(lines)


def golden_params(names: Iterable[str]) -> list[object]:
    return [
        pytest.param(name, scale, marks=pytest.mark.fixtures(f"screenshots/{name}.webp"), id=f"{name}-{scale}")
        for name in names
        for scale in SCALES
    ]


GOLDEN: Final[dict[str, tuple[ImprintMode, frozenset[str] | None, ImprintGrade]]] = {
    # truth by eye (scratch truth.json, 2026-10-04); Charles' lit squares measured by the prototype (left + bottom)
    "heroinfo_haru": (ImprintMode.TEAM, ALL_LIT, ImprintGrade.B),
    "heroinfo_lots": (ImprintMode.TEAM, ALL_LIT, ImprintGrade.SSS),  # bottom square partly under the letters
    "heroinfo_ainz": (ImprintMode.SELF, None, ImprintGrade.SSS),
    "heroinfo_straze": (ImprintMode.SELF, None, ImprintGrade.SSS),
    "heroinfo_politis": (ImprintMode.TEAM, ALL_LIT, ImprintGrade.B),
    "heroinfo_charles": (ImprintMode.TEAM, frozenset({"left", "bottom"}), ImprintGrade.B),
    "equip_renoa": (ImprintMode.TEAM, ALL_LIT, ImprintGrade.SSS),
    "equip_haru": (ImprintMode.TEAM, ALL_LIT, ImprintGrade.B),
    "equip_straze": (ImprintMode.SELF, None, ImprintGrade.SSS),
}
LETTERS: Final = {ImprintGrade.B: 1, ImprintGrade.SSS: 3}
COLOURS: Final = {ImprintGrade.B: "blue", ImprintGrade.SSS: "red"}


@pytest.mark.parametrize(("name", "scale"), golden_params(GOLDEN))
def test_real_capture(name: str, scale: float) -> None:
    image, screen = golden_screen(name, scale)
    mode, lit, grade = GOLDEN[name]
    reading = read_imprint_icon(image, screen.anchors)
    assert (reading.mode, reading.locked, reading.colour) == (mode, False, COLOURS[grade])
    assert (reading.letters, reading.grade, reading.grade_confidence) == (LETTERS[grade], grade, 0.9)
    assert reading.lit == (None if lit is None else lit_states(lit))
    assert reading.mode_confidence >= 0.8
    assert reading.warnings == ()
    assert locked_mismatch(reading, screen.imprint_locked) is None


@pytest.mark.parametrize(("name", "scale"), golden_params(GOLDEN))
def test_real_capture_windows_without_icon(name: str, scale: float) -> None:
    """Negative controls: windows of the icon window's size over the imprint text, above the level line and over the
    hero art must stay unknown."""
    image, screen = golden_screen(name, scale)
    level, cp, text = screen.anchors.level, screen.anchors.cp, screen.anchors.imprint
    assert level is not None and cp is not None and text
    line = float(np.median([b.height for b in text]))
    x0, x1 = int(level.x0 - 0.6 * level.height), int(min(b.x0 for b in text))
    y0, y1 = int(level.y1), int(cp.y0)
    width, height = x1 - x0, y1 - y0
    windows = {
        "imprint text": (x1, y0, x1 + width, y1),
        "above the level line": (x0, y0 - 2 * height, x1, y0 - height),
        "hero art": (image.shape[1] // 2, y0, image.shape[1] // 2 + width, y1),
    }
    for where, (a0, b0, a1, b1) in windows.items():
        reading = read_icon_window(image[max(0, b0) : b1, max(0, a0) : a1], line)
        assert (reading.mode, reading.locked, reading.grade) == (None, False, None), where


CROPS: Final[dict[int, tuple[ImprintMode | None, frozenset[str] | None, ImprintGrade | None]]] = {
    5: (None, None, None),  # "Locked"
    6: (ImprintMode.SELF, None, ImprintGrade.B),
    7: (ImprintMode.TEAM, ALL_LIT, ImprintGrade.SSS),
    8: (ImprintMode.TEAM, frozenset({"right", "bottom"}), ImprintGrade.SSS),  # lit squares: by eye, provisional
}
"""The user's zoomed icon crops (~2.5x), copied as fixtures/screenshots/imprint_crop_<n>.png."""


def _crop_anchors(lines: Sequence[TextLine]) -> ScreenAnchors:
    """A crop has no level line: the imprint text block is the top text line and the lines wrapped under it."""
    ordered = sorted(lines, key=lambda ln: (ln.box.y0, ln.box.x0))
    block = [ordered[0]]
    for line in ordered[1:]:
        last = block[-1]
        if (
            abs(line.box.x0 - block[0].box.x0) < 0.6 * last.box.height
            and line.box.y0 < last.box.y1 + 0.6 * last.box.height
        ):
            block.append(line)
    return ScreenAnchors(imprint=tuple(line.box for line in block))


@pytest.mark.parametrize(
    ("crop", "scale"),
    [
        pytest.param(n, s, marks=pytest.mark.fixtures(f"screenshots/imprint_crop_{n}.png"), id=f"crop{n}-{s}")
        for n in CROPS
        for s in SCALES
    ],
)
def test_real_icon_crop(crop: int, scale: float) -> None:
    image, lines = capture(f"screenshots/imprint_crop_{crop}.png", scale)
    mode, lit, grade = CROPS[crop]
    reading = read_imprint_icon(image, _crop_anchors(lines))
    assert (reading.mode, reading.grade) == (mode, grade)
    assert reading.locked is (crop == 5)
    assert reading.lit == (None if lit is None else lit_states(lit))
    assert reading.warnings == ()
