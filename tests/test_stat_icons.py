"""Stat-icon classifier: synthetic glyphs (always run) and the user's Hero Info captures (local fixtures only).

The synthetic icons are simple shapes drawn here, not the game's artwork: they only exercise the mechanics (templates
cut left of the labels, polarity-free matching on varied backgrounds, consensus per group, margin rule).
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from statistics import median
from typing import Final

import cv2
import numpy as np
import numpy.typing as npt
import pytest

from e7ac.domain.codes import Stat
from e7ac.vision.ocr import Box, TextLine
from e7ac.vision.stat_icons import (
    ICON_STATS,
    TEMPLATE_SIZE_RANGE,
    IconError,
    IconMatch,
    IconWindow,
    StatTemplates,
    build_templates,
    match_icons,
)
from tests.markers import FIXTURES_DIR

type Image = npt.NDArray[np.uint8]

# ------------------------------------------------------------------------------------------------ synthetic screen

CANVAS: Final = 64
"""Glyphs are drawn on a 64x64 canvas, then resized (anti-aliased) to the icon size."""
DRAW: Final = 255


def _poly(canvas: Image, points: Sequence[tuple[int, int]], thickness: int = -1, closed: bool = True) -> None:
    pts = np.array(points, np.int32).reshape(-1, 1, 2)
    if thickness < 0:
        cv2.fillPoly(canvas, [pts], DRAW)
    else:
        cv2.polylines(canvas, [pts], closed, DRAW, thickness)


def _dagger(c: Image) -> None:
    cv2.line(c, (32, 6), (32, 58), DRAW, 8)
    cv2.line(c, (16, 20), (48, 20), DRAW, 7)


def _shield(c: Image) -> None:
    _poly(c, [(10, 8), (54, 8), (54, 34), (32, 57), (10, 34)], thickness=6)


def _heart(c: Image) -> None:
    cv2.circle(c, (21, 23), 13, DRAW, -1)
    cv2.circle(c, (43, 23), 13, DRAW, -1)
    _poly(c, [(9, 28), (55, 28), (32, 56)])


def _chevrons(c: Image) -> None:
    _poly(c, [(8, 10), (28, 32), (8, 54)], thickness=8, closed=False)
    _poly(c, [(32, 10), (52, 32), (32, 54)], thickness=8, closed=False)


def _triangle(c: Image) -> None:
    _poly(c, [(32, 6), (58, 56), (6, 56)])


def _sun(c: Image) -> None:
    cv2.circle(c, (32, 32), 11, DRAW, -1)
    for angle in np.arange(0, 2 * np.pi, np.pi / 4):
        dx, dy = np.cos(angle), np.sin(angle)
        cv2.line(c, (round(32 + 17 * dx), round(32 + 17 * dy)), (round(32 + 27 * dx), round(32 + 27 * dy)), DRAW, 6)


def _cross(c: Image) -> None:
    cv2.line(c, (9, 9), (55, 55), DRAW, 8)
    cv2.line(c, (55, 9), (9, 55), DRAW, 8)


def _diamond(c: Image) -> None:
    _poly(c, [(32, 5), (59, 32), (32, 59), (5, 32)])


def _slashed_ring(c: Image) -> None:
    cv2.circle(c, (32, 32), 23, DRAW, 6)
    cv2.line(c, (16, 48), (48, 16), DRAW, 7)


def _hook(c: Image) -> None:
    """Not one of the nine families: the "unknown icon" negative control."""
    _poly(c, [(12, 8), (12, 50), (30, 56), (50, 40), (50, 26)], thickness=7, closed=False)
    cv2.circle(c, (50, 18), 6, DRAW, -1)


GLYPHS: Final[dict[Stat, Callable[[Image], None]]] = {
    Stat.ATK: _dagger,
    Stat.DEF: _shield,
    Stat.HP: _heart,
    Stat.SPEED: _chevrons,
    Stat.CRIT_CHANCE: _triangle,
    Stat.CRIT_DAMAGE: _sun,
    Stat.EFFECTIVENESS: _cross,
    Stat.EFFECT_RESISTANCE: _diamond,
    Stat.DUAL_ATTACK: _slashed_ring,
}

PITCH: Final = 40  # label row pitch of the synthetic screen (~ the user's captures at UI scale 1.0)
LABEL_X0: Final = 120
LABEL_ICON: Final = 28  # label icon size, px (0.7 pitch)
SUB_PITCH: Final = 34  # gear substat row pitch (0.85 label pitch, as measured)
PANEL: Final = (34, 20, 28)  # BGR: dark stats panel
DARK_RED: Final = (30, 24, 112)
PURPLE: Final = (112, 38, 100)
PINK: Final = (210, 178, 245)  # the bright nebula: a grey icon is darker than it
GREY: Final = (200, 200, 200)
WHITE: Final = (255, 255, 255)


def _alpha(draw: Callable[[Image], None], size: int) -> npt.NDArray[np.float32]:
    canvas = np.zeros((CANVAS, CANVAS), np.uint8)
    draw(canvas)
    resized = cv2.resize(canvas, (size, size), interpolation=cv2.INTER_AREA)
    return (np.asarray(resized, np.float32) / 255).astype(np.float32)


def _paint(
    image: Image,
    draw: Callable[[Image], None],
    cx: float,
    cy: float,
    size: int,
    colour: tuple[int, int, int],
    opacity: float = 1.0,
) -> None:
    """Alpha-blend a glyph of `size` px centred on (cx, cy)."""
    alpha = _alpha(draw, size)[..., None] * opacity
    x0, y0 = round(cx - size / 2), round(cy - size / 2)
    roi = image[y0 : y0 + size, x0 : x0 + size].astype(np.float32)
    blended = roi * (1 - alpha) + np.array(colour, np.float32) * alpha
    image[y0 : y0 + size, x0 : x0 + size] = np.clip(blended + 0.5, 0, 255).astype(np.uint8)


def _noisy(image: Image, sigma: float = 2.0) -> Image:
    rng = np.random.default_rng(7)
    return np.clip(image.astype(np.float32) + rng.normal(0, sigma, image.shape), 0, 255).astype(np.uint8)


def label_boxes() -> dict[Stat, Box]:
    return {
        stat: Box(LABEL_X0, 80 + PITCH * i - 13, LABEL_X0 + 150, 80 + PITCH * i + 13)
        for i, stat in enumerate(ICON_STATS)
    }


def stats_panel(width: int = 960, height: int = 720, skip: Stat | None = None) -> Image:
    """A dark stats panel with the nine label icons (and some label text) at a label pitch of 40 px."""
    image = np.full((height, width, 3), PANEL, np.uint8)
    for stat, box in label_boxes().items():
        if stat != skip:
            _paint(image, GLYPHS[stat], LABEL_X0 - 0.85 * PITCH, box.cy, LABEL_ICON, GREY)
        cv2.putText(image, stat.name.title(), (LABEL_X0, round(box.y1) - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.6, GREY, 1)
    return image


@dataclass(frozen=True, slots=True)
class Icon:
    """One drawn gear icon: glyph (None = blank row), scale vs the label icon, colour and opacity."""

    glyph: Callable[[Image], None] | None
    scale: float = 0.9
    colour: tuple[int, int, int] = GREY
    opacity: float = 0.6
    digits: str = "12%"


BACKGROUNDS: Final = (DARK_RED, PURPLE, PINK)
BACKGROUND_BLUR: Final = 12  # px: smooth transitions, like the nebula behind the gear panel
GEAR_X0: Final = 470  # the gear panel starts here (the stats panel and its templates are left of it)


def gear_screen(columns: dict[str, tuple[float, list[Icon]]]) -> tuple[Image, list[IconWindow]]:
    """Stats panel + gear icon columns over a smooth background cycling through dark red, purple and bright pink
    row by row, each icon followed by digits; returns the image and one generous window per row (as the gear-panel
    reader builds them)."""
    image = stats_panel()
    top = 40
    band = np.zeros_like(image)
    for row in range(-1, (image.shape[0] - top) // SUB_PITCH + 2):
        y0 = max(0, top + row * SUB_PITCH - SUB_PITCH // 2)
        band[y0 : top + row * SUB_PITCH + SUB_PITCH // 2, :] = BACKGROUNDS[row % 3]
    image[:, GEAR_X0:] = np.asarray(cv2.GaussianBlur(band, (0, 0), BACKGROUND_BLUR), np.uint8)[:, GEAR_X0:]
    windows: list[IconWindow] = []
    for group, (cx, icons) in columns.items():
        for row, icon in enumerate(icons):
            cy = top + row * SUB_PITCH
            if icon.glyph is not None:
                size = round(LABEL_ICON * icon.scale)
                _paint(image, icon.glyph, cx, cy, size, icon.colour, icon.opacity)
                origin = (round(cx + 0.8 * SUB_PITCH), round(cy + 9))
                cv2.putText(image, icon.digits, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.7, icon.colour, 2)
            box = Box(cx - 1.5 * SUB_PITCH, cy - 0.75 * SUB_PITCH, cx + 1.6 * SUB_PITCH, cy + 0.75 * SUB_PITCH)
            windows.append(IconWindow(box, group))
    return _noisy(image), windows


def families(matches: Sequence[IconMatch]) -> list[Stat | None]:
    return [m.family for m in matches]


SUBS: Final = [Icon(GLYPHS[stat]) for stat in ICON_STATS]
MAINS: Final = [Icon(GLYPHS[stat], scale=1.0, colour=WHITE, opacity=1.0) for stat in ICON_STATS]


# ------------------------------------------------------------------------------------------------ templates


def test_templates_are_cut_left_of_each_label() -> None:
    templates = build_templates(_noisy(stats_panel()), label_boxes())
    assert tuple(templates.crops) == ICON_STATS
    assert templates.pitch == PITCH
    low, high = TEMPLATE_SIZE_RANGE
    for crop in templates.crops.values():
        assert crop.ndim == 3
        assert low <= max(crop.shape[:2]) / PITCH <= high


def test_templates_fail_closed() -> None:
    image, labels = _noisy(stats_panel()), label_boxes()
    with pytest.raises(IconError, match=r"stat labels not found .*DUAL_ATTACK"):
        build_templates(image, {s: b for s, b in labels.items() if s != Stat.DUAL_ATTACK})
    swapped = {**labels, Stat.ATK: labels[Stat.DEF], Stat.DEF: labels[Stat.ATK]}
    with pytest.raises(IconError, match="order"):
        build_templates(image, swapped)
    far = labels[Stat.DUAL_ATTACK]
    uneven = {**labels, Stat.DUAL_ATTACK: Box(far.x0, far.y0 + PITCH, far.x1, far.y1 + PITCH)}
    with pytest.raises(IconError, match="evenly spaced"):
        build_templates(image, uneven)
    with pytest.raises(IconError, match="no icon found left of the SPEED label"):
        build_templates(stats_panel(skip=Stat.SPEED), labels)  # flat panel, no speed icon
    with pytest.raises(IconError, match="BGR uint8"):
        build_templates(np.asarray(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), np.uint8), labels)


def test_template_size_is_checked() -> None:
    """A blob much bigger than an icon left of a label is not taken as its template."""
    image = stats_panel()
    cy = label_boxes()[Stat.HP].cy
    cv2.rectangle(image, (LABEL_X0 - 62, round(cy - 22)), (LABEL_X0 - 6, round(cy + 22)), GREY, -1)
    with pytest.raises(IconError, match=r"HP icon is .* label pitches wide"):
        build_templates(image, label_boxes())


def test_stat_templates_validate_their_content() -> None:
    crop = np.zeros((10, 10, 3), np.uint8)
    with pytest.raises(IconError, match="at least two"):
        StatTemplates({Stat.ATK: crop}, 40.0)
    with pytest.raises(IconError, match="unknown"):
        StatTemplates({Stat.ATK: crop, Stat.ATK_PERCENT: crop}, 40.0)
    with pytest.raises(IconError, match="positive"):
        StatTemplates({Stat.ATK: crop, Stat.HP: crop}, 0.0)
    with pytest.raises(IconError, match="BGR image"):
        StatTemplates({Stat.ATK: crop, Stat.HP: crop[:, :, 0]}, 40.0)


# ------------------------------------------------------------------------------------------------ matching


@cache
def _synthetic_run() -> tuple[list[IconMatch], list[str]]:
    """Substats (grey, semi-transparent, 0.9x) and main stats (white, opaque, 1.0x) of every family, a blank row
    and an unknown glyph in each column, and an EE-like single icon at 0.65x (as measured)."""
    blank, unknown = Icon(None), Icon(_hook)
    image, windows = gear_screen(
        {
            "L.sub": (560, [*SUBS, blank, unknown, Icon(None, digits="")]),
            "R.main": (820, [*MAINS, blank, Icon(_hook, scale=1.0, colour=WHITE, opacity=1.0)]),
        }
    )
    ee_image, ee_windows = gear_screen({"ee": (690, [Icon(None), Icon(_triangle, scale=0.65, opacity=0.9)])})
    image[:, 640:760] = ee_image[:, 640:760]
    windows.append(IconWindow(ee_windows[1].box, "ee"))
    templates = build_templates(image, label_boxes())
    return match_icons(image, templates, windows), [w.group for w in windows]


def test_every_family_is_recognised_on_every_background() -> None:
    matches, groups = _synthetic_run()
    subs = [m for m, g in zip(matches, groups, strict=True) if g == "L.sub"]
    mains = [m for m, g in zip(matches, groups, strict=True) if g == "R.main"]
    assert families(subs[:9]) == list(ICON_STATS)  # grey 60% icons on red, purple and bright pink
    assert families(mains[:9]) == list(ICON_STATS)  # white opaque icons
    for match in [*subs[:9], *mains[:9]]:
        assert match.family == match.best
        assert match.score >= 0.45 and match.margin >= 0.15
        assert match.box is not None


def test_dual_attack_is_reported_never_remapped() -> None:
    matches, _ = _synthetic_run()
    assert matches[8].family == Stat.DUAL_ATTACK  # the caller flags it: not a gear stat


def test_blank_windows_and_unknown_glyphs_abstain() -> None:
    matches, groups = _synthetic_run()
    subs = [m for m, g in zip(matches, groups, strict=True) if g == "L.sub"]
    mains = [m for m, g in zip(matches, groups, strict=True) if g == "R.main"]
    assert families([subs[9], subs[11], mains[9]]) == [None, None, None]  # blank rows
    assert families([subs[10], mains[10]]) == [None, None]  # a glyph that is none of the nine


def test_single_window_group_uses_its_own_column_and_scale() -> None:
    matches, groups = _synthetic_run()
    assert groups[-1] == "ee"
    assert matches[-1].family == Stat.CRIT_CHANCE


LOOKALIKE: Final = (Stat.EFFECT_RESISTANCE, Stat.HP)
"""Synthetic diamond and heart share their V-shaped lower half (in the game: the heart and the shield)."""


def test_icons_without_their_template_abstain_unless_a_lookalike_exists() -> None:
    """The true template removed (an icon the templates do not know): the margin rule rejects the impostor, except
    for a lookalike, which can win clearly (the game's heart without its template became the shield with margins up
    to 0.22). That is why build_templates fails closed instead of returning an incomplete set."""
    image, windows = gear_screen({"L.sub": (560, SUBS), "R.main": (820, MAINS)})
    templates = build_templates(image, label_boxes())
    for index, stat in enumerate(ICON_STATS):
        reduced = StatTemplates({s: c for s, c in templates.crops.items() if s != stat}, templates.pitch)
        found = match_icons(image, reduced, windows)
        expected = LOOKALIKE[1] if stat == LOOKALIKE[0] else None
        assert [found[index].family, found[9 + index].family] == [expected, expected], stat


def test_group_of_blank_windows_abstains_everywhere() -> None:
    image, windows = gear_screen({"L.sub": (560, [Icon(None)] * 5)})
    matches = match_icons(image, build_templates(image, label_boxes()), windows)
    assert families(matches) == [None] * 5


def test_order_is_kept_across_interleaved_groups_and_odd_windows() -> None:
    image, windows = gear_screen({"L.sub": (560, SUBS[:4]), "R.main": (820, MAINS[4:8])})
    interleaved = [w for pair in zip(windows[:4], windows[4:], strict=True) for w in pair]
    off_image = IconWindow(Box(5000, 5000, 5100, 5060), "L.sub")
    tiny = IconWindow(Box(560, 40, 566, 46), "tiny")  # smaller than any template
    templates = build_templates(image, label_boxes())
    matches = match_icons(image, templates, [*interleaved, off_image, tiny])
    expected = [s for pair in zip(ICON_STATS[:4], ICON_STATS[4:8], strict=True) for s in pair]
    assert families(matches) == [*expected, None, None]
    for odd in matches[-2:]:
        assert (odd.box, odd.score, odd.margin) == (None, 0.0, 0.0)
    assert match_icons(image, templates, []) == []


# ------------------------------------------------------------------------------------------------ real captures

A, D, H, S = Stat.ATK, Stat.DEF, Stat.HP, Stat.SPEED
CC, CD, EF, ER = Stat.CRIT_CHANCE, Stat.CRIT_DAMAGE, Stat.EFFECTIVENESS, Stat.EFFECT_RESISTANCE
type Piece = tuple[tuple[str, Stat], ...]

GOLDEN: Final[dict[str, tuple[Piece, ...]]] = {
    # Transcribed by eye from the user's captures (2026-10-04) and checked on contact sheets. Pieces in slot order:
    # weapon, helmet, armor (left column), necklace, ring, boots (right column); main stat first, then substats.
    "haru": (
        (("525", A), ("231", H), ("9%", CD), ("15%", ER), ("19%", CC)),
        (("2,765", H), ("13%", H), ("13%", D), ("12", S), ("14%", CD)),
        (("310", D), ("17%", H), ("8%", D), ("15", S), ("9%", CC)),
        (("70%", CD), ("25%", H), ("3", S), ("242", H), ("14%", CC)),
        (("65%", H), ("12", S), ("14%", CD), ("9%", CC), ("15%", ER)),
        (("45", S), ("8%", CD), ("9%", CC), ("32%", A), ("14%", H)),
    ),
    "lots": (
        (("515", A), ("12%", A), ("11%", H), ("17", S), ("6%", EF)),
        (("2,765", H), ("12%", A), ("11%", H), ("17", S), ("6%", EF)),
        (("310", D), ("17%", H), ("20%", D), ("740", H), ("9%", EF)),
        (("65%", H), ("11%", A), ("11%", D), ("17", S), ("6%", EF)),
        (("65%", H), ("207", A), ("8%", EF), ("4", S), ("14%", CC)),
        (("45", S), ("21%", A), ("21%", H), ("6%", D), ("14%", EF)),
    ),
    "ainz": (
        (("500", A), ("10%", CC), ("9%", EF), ("15%", A), ("8%", H)),
        (("2,700", H), ("4%", EF), ("12%", CC), ("17%", H), ("12%", A)),
        (("300", D), ("375", H), ("9%", CC), ("14%", CD), ("11%", EF)),
        (("60%", H), ("6%", EF), ("21%", A), ("86", A), ("6", S)),
        (("60%", EF), ("13%", D), ("25%", ER), ("14%", A), ("4", S)),
        (("40", S), ("8%", A), ("8%", H), ("711", H), ("10%", CD)),
    ),
    "straze": (
        (("525", A), ("7%", EF), ("13%", CD), ("13%", CC), ("24%", A)),
        (("2,835", H), ("24%", A), ("12%", CC), ("8%", EF), ("10%", CD)),
        (("310", D), ("28%", CD), ("16%", CC), ("8%", EF), ("9%", ER)),
        (("70%", CD), ("145", A), ("33%", A), ("9%", H), ("6%", CC)),
        (("65%", A), ("6%", CC), ("53", A), ("30%", CD), ("6", S)),
        (("65%", A), ("10%", CC), ("14%", CD), ("9", S), ("56", A)),
    ),
    "politis": (  # the +0 weapon has 3 substats: its 4th row is empty (a blank window)
        (("100", A), ("187", H), ("4", S), ("8%", A)),
        (("2,700", H), ("13%", D), ("6%", A), ("7%", H), ("38%", ER)),
        (("60", D), ("3", S), ("8%", H), ("5%", ER), ("201", H)),
        (("12%", H), ("8%", D), ("3", S), ("6%", ER), ("6%", EF)),
        (("12%", H), ("8%", D), ("4", S), ("6%", ER), ("6%", EF)),
        (("45", S), ("99", A), ("229", H), ("41%", A), ("9%", H)),
    ),
}
EE_FAMILY: Final = {"straze": CC}  # Star Extinction "12%": the Crit Hit Chance icon (0.65x), visually confirmed
REAL_SCALES: Final = (0.64, 1.0, 1.28)  # 1.28 x the 2000-px copies = the user's 2560x1494 window
_VALUE: Final = re.compile(r"^\d{1,3}(?:,\d{3})*%?$")
_EE_VALUE: Final = re.compile(r"^\d{1,3}%$")

type Key = tuple[int, int, int]  # (column 0 = left / 1 = right, piece 0-2 top to bottom, row 0 = main, 1-4 = subs)


def truth(name: str) -> dict[Key, tuple[str, Stat]]:
    return {
        (slot // 3, slot % 3, row): value for slot, piece in enumerate(GOLDEN[name]) for row, value in enumerate(piece)
    }


@dataclass(frozen=True, slots=True)
class GearRow:
    key: Key
    text: str
    box: Box
    right_edge: float


@dataclass(frozen=True, slots=True)
class GearLayout:
    """Test-side port of the prototype's gear layout (spikes/m7_stat_icons.py `gear_layout`): the number-only lines
    of the gear panel, right-aligned in two columns; a piece starts after a gap; its main stat is confirmed by the
    other column starting a piece on the same row, or by a taller line."""

    rows: list[GearRow]
    pitch: float
    main_rows: list[float]
    gap1: float
    right_edges: tuple[float, ...]
    panel: Box


def gear_layout(lines: Sequence[TextLine], label_pitch: float, width: float) -> GearLayout:
    anchor = next((ln.box for ln in lines if "equipment score" in ln.text.casefold()), None)
    px, py = (anchor.x0, anchor.y1) if anchor else (width / 2, 0.0)
    values = [
        ln
        for ln in lines
        if _VALUE.match(ln.text.replace(" ", "")) and (ln.box.x0 + ln.box.x1) / 2 > px and ln.box.cy > py
    ]
    clusters: list[list[TextLine]] = []
    for ln in sorted(values, key=lambda c: c.box.x1):
        if clusters and abs(ln.box.x1 - median(c.box.x1 for c in clusters[-1])) <= 0.35 * label_pitch:
            clusters[-1].append(ln)
        else:
            clusters.append([ln])
    columns = sorted(sorted(clusters, key=len, reverse=True)[:2], key=lambda c: median(x.box.x1 for x in c))
    assert len(columns) == 2 and min(len(c) for c in columns) >= 5, [len(c) for c in columns]
    heights = sorted(ln.box.height for c in columns for ln in c)
    sub_h = median(heights[: max(1, len(heights) * 2 // 3)])
    cols = [sorted(c, key=lambda ln: ln.box.cy) for c in columns]
    steps = [(a, b.box.cy - a.box.cy) for c in cols for a, b in itertools.pairwise(c)]
    pitch = median(step for _, step in steps if step < 1.5 * sub_h)
    starts = [[ln for i, ln in enumerate(c) if i == 0 or ln.box.cy - c[i - 1].box.cy > 1.7 * pitch] for c in cols]
    mains = [
        ln
        for ci in (0, 1)
        for ln in starts[ci]
        if any(abs(o.box.cy - ln.box.cy) < 0.4 * pitch for o in starts[1 - ci]) or ln.box.height > 1.15 * sub_h
    ]
    main_rows: list[float] = []
    for y in sorted(m.box.cy for m in mains):
        if not main_rows or y - main_rows[-1] > 2 * pitch:
            main_rows.append(y)
    gap1 = median(step for line, step in steps if line in mains and step < 1.6 * pitch)
    edges = tuple(float(median(x.box.x1 for x in c)) for c in columns)
    rows: list[GearRow] = []
    for ci, c in enumerate(cols):
        for ln in c:
            above = [y for y in main_rows if y <= ln.box.cy + 0.4 * pitch]
            assert above, ln
            index = 0 if ln in mains else 1 + round((ln.box.cy - above[-1] - gap1) / pitch)
            rows.append(GearRow((ci, len(above) - 1, index), ln.text, ln.box, edges[ci]))
    return GearLayout(rows, pitch, main_rows, gap1, edges, Box(px, py, width, py))


def icon_window(right_edge: float, cy: float, pitch: float, value_x0: float | None, group: str) -> IconWindow:
    """The window the gear-panel reader supplies: [right - 4.6 p, min(value left, right - 1.5 p)] x cy +- 0.75 p."""
    right = right_edge - 1.5 * pitch if value_x0 is None else min(value_x0, right_edge - 1.5 * pitch)
    return IconWindow(Box(right_edge - 4.6 * pitch, cy - 0.75 * pitch, right, cy + 0.75 * pitch), group)


@dataclass(frozen=True, slots=True)
class GoldenRun:
    texts: dict[Key, str]
    icons: dict[Key, IconMatch]
    blanks: list[IconMatch]
    ee: list[IconMatch]
    unknown: dict[Key, IconMatch]
    """With the true family's template removed (only computed at UI scale 1.0: 8 extra runs per capture)."""


@cache
def golden_run(name: str, scale: float) -> GoldenRun:
    """OCR + layout + icons of one capture at one UI scale (cached: OCR is the slow part)."""
    from e7ac.vision.hero_screen import parse_hero_screen
    from e7ac.vision.image import load_image
    from e7ac.vision.ocr import RapidOcrReader

    image = load_image(FIXTURES_DIR / f"screenshots/heroinfo_{name}.webp")
    if scale != 1.0:
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=interpolation)
    lines = RapidOcrReader().read(image)
    labels = parse_hero_screen(lines).anchors.labels
    label_rows = sorted(box.cy for box in labels.values())
    label_pitch = median(b - a for a, b in itertools.pairwise(label_rows))
    layout = gear_layout(lines, label_pitch, image.shape[1])
    p = layout.pitch
    side = "LR"
    windows = [
        icon_window(r.right_edge, r.box.cy, p, r.box.x0, f"{side[r.key[0]]}.{'main' if r.key[2] == 0 else 'sub'}")
        for r in layout.rows
    ]
    known = truth(name)
    blank_windows: list[IconWindow] = []
    for ci, edge in enumerate(layout.right_edges):
        for pi, y_main in enumerate(layout.main_rows):
            if (ci, pi, 0) in known and (ci, pi, 4) not in known:  # empty 4th substat slot
                blank_windows.append(icon_window(edge, y_main + layout.gap1 + 3 * p, p, None, f"{side[ci]}.sub"))
            if pi + 1 < len(layout.main_rows):  # between two pieces
                y = (y_main + layout.gap1 + 3 * p + layout.main_rows[pi + 1]) / 2
                blank_windows.append(icon_window(edge, y, p, None, f"{side[ci]}.sub"))
    ee_lines = [  # the EE value sits above the "Average Equipment Score" line
        ln
        for ln in lines
        if _EE_VALUE.match(ln.text.strip()) and ln.box.y1 <= layout.panel.y0 and ln.box.x0 >= layout.panel.x0 - p
    ]
    ee_windows = [icon_window(ln.box.x1, ln.box.cy, p, None, "ee") for ln in ee_lines]
    templates = build_templates(image, labels)
    everything = [*windows, *blank_windows, *ee_windows]
    found = match_icons(image, templates, everything)
    keys = [r.key for r in layout.rows]
    unknown: dict[Key, IconMatch] = {}
    if scale == 1.0:
        for stat in ICON_STATS:
            reduced = StatTemplates({s: c for s, c in templates.crops.items() if s != stat}, templates.pitch)
            idx = [i for i, k in enumerate(keys) if k in known and known[k][1] == stat]
            if idx:
                again = match_icons(image, reduced, everything)
                unknown.update({keys[i]: again[i] for i in idx})
    n, b = len(windows), len(blank_windows)
    return GoldenRun(
        texts={r.key: r.text for r in layout.rows},
        icons=dict(zip(keys, found[:n], strict=True)),
        blanks=found[n : n + b],
        ee=found[n + b :],
        unknown=unknown,
    )


CAPTURES: Final = [pytest.param(n, marks=pytest.mark.fixtures(f"screenshots/heroinfo_{n}.webp")) for n in GOLDEN]


@pytest.mark.parametrize("scale", REAL_SCALES)
@pytest.mark.parametrize("name", CAPTURES)
def test_real_gear_icons(name: str, scale: float) -> None:
    run = golden_run(name, scale)
    expected = truth(name)
    assert set(run.icons) == set(expected)
    assert {k: run.texts[k] for k in expected} == {k: v[0] for k, v in expected.items()}  # the rows are indexed right
    wrong = {
        k: (m.family, expected[k][1], round(m.score, 3), round(m.margin, 3))
        for k, m in run.icons.items()
        if m.family != expected[k][1]
    }
    assert wrong == {}


@pytest.mark.parametrize("scale", REAL_SCALES)
@pytest.mark.parametrize("name", CAPTURES)
def test_real_blank_windows_abstain(name: str, scale: float) -> None:
    run = golden_run(name, scale)
    assert run.blanks  # gaps between pieces (and Politis's empty 4th substat)
    assert [m.family for m in run.blanks] == [None] * len(run.blanks)


@pytest.mark.parametrize("scale", REAL_SCALES)
@pytest.mark.parametrize("name", CAPTURES)
def test_real_exclusive_equipment_icon(name: str, scale: float) -> None:
    run = golden_run(name, scale)
    assert [m.family for m in run.ee] == ([EE_FAMILY[name]] if name in EE_FAMILY else [])


@pytest.mark.parametrize("name", CAPTURES)
def test_real_icons_without_their_template_abstain(name: str) -> None:
    run = golden_run(name, 1.0)
    assert set(run.unknown) == set(truth(name))
    accepted = {
        k: (m.family, round(m.score, 3), round(m.margin, 3)) for k, m in run.unknown.items() if m.family is not None
    }
    assert accepted == {}


@pytest.mark.fixtures("screenshots/heroinfo_charles.webp")
def test_real_templates_on_a_hero_without_gear() -> None:
    from e7ac.vision.hero_screen import parse_hero_screen
    from e7ac.vision.image import load_image
    from e7ac.vision.ocr import RapidOcrReader

    image = load_image(FIXTURES_DIR / "screenshots/heroinfo_charles.webp")
    templates = build_templates(image, parse_hero_screen(RapidOcrReader().read(image)).anchors.labels)
    assert tuple(templates.crops) == ICON_STATS
