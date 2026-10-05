"""Read the star row next to the hero name (M7): number of stars and awakened stars.

MECH-HERO-03: the stars next to the hero name show the awakening; an awakened star is multicoloured (yellow-orange
top, magenta-pink lower half) with a dark centre, a plain star is uniform yellow. MECH-HERO-01 (community): the level
cap is stars x 10; MECH-HERO-02 (assumed): awakening never exceeds the star count.

Evidence: prototype `spikes/m7_imprint_stars.py` on the user's own captures (2026-10-04, Stove PC client, English:
6 Hero Info + 3 Equipment captures at UI scales 0.64 / 1.0 / 1.28).
- Result: stars and awakened 9/9 at every scale, 0 wrong. 0 wrong also at 0.50 and 0.40 and on JPEG q50, blur,
  noise, darker and brighter copies (a few abstentions).
- Negative controls: the reader next to the level and CP lines (orange "Lv. Max/60" text, gold set icons) gave None
  18/18. Next to every other OCR line of the captures it found 2-3 false rows of 1-3 "stars" from orange badges per
  scale, so the name anchor is essential; a single star gets a low confidence and the level-cap cross-check flags the
  rest.
- Measured: star top 0.24-0.33 name-line heights below the name box top, pitch 0.33-0.48 name-line heights; plain
  stars: centre / own yellow brightness 1.00-1.01, no pink; awakened stars: 0.30-0.52 and pink 0.42-0.62 of the lower
  half. Politis (5 stars) has only the FIRST star awakened: the only partial awakening seen.

Method:
1. band: the OCR name line (which often includes the stars, e.g. "Haru ☆") extended 6 line heights to the right;
2. yellow mask (relative to the band's brightest yellow); components with the same top form the row;
3. star tips: one column run per star in the top 30 % of the row; the chain must be regular (each gap 0.8-1.25
   pitch) and pass a shape check (narrow tip, wide arms, mirror symmetry, pitch and top offset against the name line),
   else the count is None (never a guess);
4. per star: centre darkness relative to the star's own yellow top plus the pink share of its lower half ->
   awakened / plain / unknown; any unknown star leaves the awakened count None.

Limits: one client, layout and language seen. Partial awakening has one sample; that awakened stars fill from the
left is assumed (a plain star before an awakened one is a warning). The yellow hue range also matches the orange
level text and gear badges: the reader trusts the name anchor it is given.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from statistics import median
from typing import Final

import cv2
import numpy as np
import numpy.typing as npt

from e7ac.vision.hero_screen import ScreenAnchors
from e7ac.vision.ocr import Box

type BgrImage = npt.NDArray[np.uint8]
type Channel = npt.NDArray[np.int32]
type Mask = npt.NDArray[np.bool_]

AWAKENED: Final = "awakened"
PLAIN: Final = "plain"
UNKNOWN: Final = "unknown"

# --- band (lengths in name-line heights h) ---
BAND_RIGHT: Final = 6.0
"""The band reaches 6 h right of the name box (the row's right end measured up to 3.0 h right of it)."""

# --- yellow star parts (OpenCV HSV: hue 0-179, saturation and value 0-255) ---
YELLOW_HUE: Final = (10, 35)
"""Every star has a yellow-orange top (plain centres measured hue 20-22)."""
YELLOW_S: Final = 100
YELLOW_V: Final = 0.6
"""Yellow pixels: S > 100 and V > 0.6 x the band's 99th-percentile yellow V (relative, survives darker copies)."""
YELLOW_V_FLOOR: Final = 60
"""The relative V threshold never goes below this (a band with only dark yellow has no stars)."""
MIN_YELLOW_AREA: Final = 0.02
"""Fewer yellow pixels than this (in h^2) in the band: no stars. Also the smallest star part kept."""
MIN_PART_HEIGHT: Final = 0.2
"""Star parts are at least 0.2 h tall (pitch 0.33-0.48 h, star parts 0.8-1.3 pitch)."""
SAME_TOP: Final = 0.08
"""Parts of one row have their tops within 0.08 h (all star tips are level) ..."""
PART_HEIGHT_RATIO: Final = (0.4, 2.5)
"""... and heights within this ratio (an awakened star's yellow part is only 0.77-1.00 pitch tall, a plain star's
1.25-1.32)."""

# --- tips and chain (lengths in star pitches p, the distance between two tips) ---
TIP_ROWS: Final = 0.3
"""Tips are read in the top 30 % of the row's height (the arms of neighbouring stars meet lower down)."""
NARROW_RUN: Final = 0.4
"""Column runs narrower than 0.4 x the median run are specks, not tips."""
CHAIN_BREAK: Final = 1.6
"""A gap above 1.6 p splits the row into chains; the longest is kept (warning)."""
REGULAR_GAP: Final = (0.8, 1.25)
"""Every gap of the kept chain must be within this many p, else the count is None."""
SINGLE_STAR_HEIGHT: Final = 1.3
"""A lone star's pitch is estimated as its yellow height / 1.3 (plain stars measured 1.25-1.32 p tall; an awakened
star's yellow part is shorter, so its estimate is too small and the shape check usually abstains)."""

# --- shape check (p units unless stated) ---
TIP_WIDTH_MAX: Final = 0.25
"""Median yellow width over the top 0.15 p (clean stars 0.05-0.18; JPEG q50 at 0.64 up to 0.32; orange text and
badges wider: the compromise between abstaining on blurred stars and accepting badges)."""
TIP_DEPTH: Final = 0.15
ARM_WIDTH_MIN: Final = 0.45
"""Widest yellow row over 0.3-0.8 p (clean 0.58-1.11, darker copies 0.53)."""
ARM_ROWS: Final = (0.3, 0.8)
SYMMETRY_MIN: Final = 0.45
"""Median mirror symmetry of the star's upper 0.8 p about its tip (clean >= 0.57)."""
SYMMETRY_HALF_WIDTH: Final = 0.45
SYMMETRY_DEPTH: Final = 0.8
SHAPE_DEPTH: Final = 0.9
"""Rows of a star used for the tip and arm widths, from its top."""
PITCH_PER_LINE: Final = (0.25, 0.6)
"""Pitch / name-line height (measured 0.33-0.48)."""
TOP_OFFSET: Final = (0.15, 0.45)
"""(Star top - name box top) / name-line height (measured 0.24-0.33)."""

# --- awakened or plain (p units) ---
CENTRE_DEPTH: Final = 0.72
"""The star centre is 0.72 p below its tip."""
CENTRE_RADIUS: Final = 0.1
"""Radius of the centre disk (at least one pixel)."""
REFERENCE_HALF_WIDTH: Final = 0.3
"""The star's own yellow brightness: median V of its row pixels within +-0.3 p of the tip."""
LOWER_ROWS: Final = (0.75, 1.2)
LOWER_HALF_WIDTH: Final = 0.35
"""The star's lower half: rows 0.75-1.2 p below the tip, +-0.35 p around it."""
PINK_HUE: Final = (140, 178)
PINK_S: Final = 80
PINK_V: Final = 120
"""Magenta-pink pixels of an awakened star's lower half."""
AWAKENED_DARK_MAX: Final = 0.70
AWAKENED_PINK_MIN: Final = 0.08
"""Awakened: centre / own yellow V <= 0.70 (measured 0.30-0.52) and pink share >= 0.08 (0.42-0.62)."""
PLAIN_BRIGHT_MIN: Final = 0.85
PLAIN_PINK_MAX: Final = 0.04
"""Plain: centre / own yellow V >= 0.85 (measured 1.00-1.01), pink share <= 0.04 (0.00) and a yellow centre."""

# --- confidence ---
CLEAN_CONFIDENCE: Final = 0.9
WARNED_CONFIDENCE: Final = 0.5
SINGLE_STAR_CONFIDENCE: Final = 0.4
"""A lone star: the pitch is estimated and most held-out false rows were single 'stars'."""


@dataclass(frozen=True, slots=True)
class StarRowReading:
    stars: int | None
    """None = no trustworthy star row next to the name."""
    awakened: int | None
    """None = not every star could be told awakened or plain."""
    per_star: tuple[str, ...]
    """Left to right: "awakened" / "plain" / "unknown" (empty when `stars` is None)."""
    confidence: float
    warnings: tuple[str, ...]


def read_star_row(image: BgrImage, anchors: ScreenAnchors, level_cap: int | None) -> StarRowReading:
    """Stars and awakened stars right of the hero name (MECH-HERO-03), cross-checked with the level cap."""
    if anchors.name is None:
        return _no_row(["no hero name line: the star row has no anchor"])
    band = _Band.cut(image, anchors.name)
    if band is None:
        return _no_row(["the hero name line is outside the image"])
    warnings: list[str] = []
    row = _yellow_row(band, warnings)
    if row is None:
        return _no_row(warnings)
    chain = _tip_chain(row, warnings)
    if chain is None or not _looks_like_stars(band, row, chain, warnings):
        return _no_row(warnings)
    per_star = tuple(_classify(band, row, tip, chain.pitch) for tip in chain.tips)
    awakened = _awakened_count(per_star, warnings)
    stars = len(per_star)
    warnings.extend(_cross_checks(stars, level_cap))
    confidence = CLEAN_CONFIDENCE if not warnings else WARNED_CONFIDENCE
    if stars == 1:
        confidence = min(confidence, SINGLE_STAR_CONFIDENCE)
    return StarRowReading(stars, awakened, per_star, confidence, tuple(warnings))


# ------------------------------------------------------------------------------------------------ internals


def _no_row(warnings: list[str]) -> StarRowReading:
    return StarRowReading(None, None, (), 0.0, tuple(warnings))


@dataclass(frozen=True, slots=True)
class _Band:
    """The image strip right of the name, in HSV; `line_height` = the name line's height in pixels."""

    h: Channel
    s: Channel
    v: Channel
    line_height: float

    @classmethod
    def cut(cls, image: BgrImage, name: Box) -> _Band | None:
        line_height = name.height
        x0, y0 = max(0, int(name.x0)), max(0, int(name.y0))
        x1 = min(image.shape[1], int(name.x1 + BAND_RIGHT * line_height))
        y1 = min(image.shape[0], int(name.y1))
        if line_height <= 0 or x1 <= x0 or y1 <= y0:
            return None
        hsv = cv2.cvtColor(np.ascontiguousarray(image[y0:y1, x0:x1]), cv2.COLOR_BGR2HSV).astype(np.int32)
        return cls(hsv[..., 0], hsv[..., 1], hsv[..., 2], line_height)

    @property
    def shape(self) -> tuple[int, int]:
        return self.v.shape[0], self.v.shape[1]


@dataclass(frozen=True, slots=True)
class _Row:
    """The star row: all yellow pixels of the band, the row's own parts, and the row's top and bottom (band rows)."""

    yellow: Mask
    parts: Mask
    top: int
    bottom: int


@dataclass(frozen=True, slots=True)
class _Chain:
    tips: tuple[float, ...]
    """Tip x positions in band columns, left to right."""
    pitch: float


def _yellow_row(band: _Band, warnings: list[str]) -> _Row | None:
    """Yellow parts with the same top form the row (the largest such group by area)."""
    h = band.line_height
    hue_ok = (band.h >= YELLOW_HUE[0]) & (band.h <= YELLOW_HUE[1]) & (band.s > YELLOW_S)
    if hue_ok.sum() < MIN_YELLOW_AREA * h * h:
        warnings.append("no yellow star parts next to the name")
        return None
    threshold = max(float(YELLOW_V_FLOOR), YELLOW_V * float(np.percentile(band.v[hue_ok], 99)))
    yellow = hue_ok & (band.v > threshold)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(yellow.astype(np.uint8), connectivity=8)
    parts = [
        i
        for i in range(1, count)
        if stats[i, cv2.CC_STAT_HEIGHT] >= MIN_PART_HEIGHT * h and stats[i, cv2.CC_STAT_AREA] >= MIN_YELLOW_AREA * h * h
    ]
    if not parts:
        warnings.append("no yellow star parts next to the name")
        return None
    groups: list[list[int]] = []
    for i in sorted(parts, key=lambda i: int(stats[i, cv2.CC_STAT_LEFT])):
        for group in groups:
            first = group[0]
            same_top = abs(int(stats[i, cv2.CC_STAT_TOP]) - int(stats[first, cv2.CC_STAT_TOP])) <= SAME_TOP * h
            ratio = stats[i, cv2.CC_STAT_HEIGHT] / stats[first, cv2.CC_STAT_HEIGHT]
            if same_top and PART_HEIGHT_RATIO[0] <= ratio <= PART_HEIGHT_RATIO[1]:
                group.append(i)
                break
        else:
            groups.append([i])
    best = max(groups, key=lambda g: sum(int(stats[i, cv2.CC_STAT_AREA]) for i in g))
    if len(groups) > 1:
        warnings.append(f"{len(groups) - 1} other yellow object(s) next to the name ignored")
    mask = np.isin(labels, best)
    rows = np.nonzero(mask.any(axis=1))[0]
    return _Row(yellow, mask, int(rows.min()), int(rows.max()))


def _tip_chain(row: _Row, warnings: list[str]) -> _Chain | None:
    """One column run per star tip; the longest regular chain of tips (None when irregular)."""
    depth = max(2, round(TIP_ROWS * (row.bottom - row.top + 1)))
    hit = row.parts[row.top : row.top + depth].any(axis=0)
    runs = _runs(hit)
    if not runs:
        warnings.append("no star tips found")
        return None
    widest = median(b - a for a, b in runs)
    narrow = [r for r in runs if r[1] - r[0] < NARROW_RUN * widest]
    if narrow:
        warnings.append(f"{len(narrow)} narrow yellow speck(s) ignored")
    centres = [(a + b - 1) / 2 for a, b in runs if b - a >= NARROW_RUN * widest]
    if len(centres) == 1:
        warnings.append("single star: pitch estimated from its height")
        return _Chain((centres[0],), (row.bottom - row.top + 1) / SINGLE_STAR_HEIGHT)
    pitch = float(median(np.diff(centres)))
    chains: list[list[float]] = [[centres[0]]]
    for left, right in pairwise(centres):
        if right - left > CHAIN_BREAK * pitch:
            chains.append([])
        chains[-1].append(right)
    best = max(chains, key=len)
    if len(chains) > 1:
        warnings.append(f"{len(chains) - 1} separate yellow group(s) beyond {CHAIN_BREAK} pitch ignored")
    if len(best) >= 2:
        pitch = float(median(np.diff(best)))
    gaps = np.diff(best)
    if not all(REGULAR_GAP[0] * pitch <= gap <= REGULAR_GAP[1] * pitch for gap in gaps):
        warnings.append(f"irregular star spacing {np.round(gaps / pitch, 2).tolist()} pitch: count not read")
        return None
    return _Chain(tuple(best), pitch)


def _runs(hit: Mask) -> list[tuple[int, int]]:
    """(start, end) of each run of True values, end exclusive."""
    padded = np.concatenate(([False], hit, [False])).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2], strict=True)]


def _looks_like_stars(band: _Band, row: _Row, chain: _Chain, warnings: list[str]) -> bool:
    """Shape check in pitch units: narrow tips, wide arms, mirror-symmetric upper parts, plausible pitch and top
    offset. Added after the orange level text and the gold set icons gave regular yellow 'tips' in the prototype."""
    p, yellow = chain.pitch, row.yellow
    tips, arms, symmetries = [], [], []
    for centre in chain.tips:
        x0, x1 = max(0, round(centre - 0.5 * p)), round(centre + 0.5 * p) + 1
        widths = yellow[row.top : int(row.top + SHAPE_DEPTH * p) + 1, x0:x1].sum(axis=1) / p
        tips.append(float(np.median(widths[: max(1, round(TIP_DEPTH * p))])))
        low, high = int(ARM_ROWS[0] * p), int(ARM_ROWS[1] * p) + 1
        arms.append(float(widths[low:high].max()) if widths.size > low else 0.0)
        symmetries.append(_mirror_symmetry(yellow, row.top, centre, p))
    pitch_ratio = p / band.line_height
    top_offset = row.top / band.line_height
    ok = (
        TOP_OFFSET[0] <= top_offset <= TOP_OFFSET[1]
        and max(tips) <= TIP_WIDTH_MAX
        and min(arms) >= ARM_WIDTH_MIN
        and float(np.median(symmetries)) >= SYMMETRY_MIN
        and PITCH_PER_LINE[0] <= pitch_ratio <= PITCH_PER_LINE[1]
    )
    if not ok:
        warnings.append(
            f"not a star row: tip width {max(tips):.2f} p (<= {TIP_WIDTH_MAX}), arm width {min(arms):.2f} p "
            f"(>= {ARM_WIDTH_MIN}), symmetry {float(np.median(symmetries)):.2f} (>= {SYMMETRY_MIN}), "
            f"pitch {pitch_ratio:.2f} h, top offset {top_offset:.2f} h"
        )
    return ok


def _mirror_symmetry(yellow: Mask, top: int, centre: float, p: float) -> float:
    """Intersection over union of the star's upper part left of its tip and the mirrored right part."""
    half, tip = round(SYMMETRY_HALF_WIDTH * p), round(centre)
    rows = slice(top, int(top + SYMMETRY_DEPTH * p) + 1)
    left = yellow[rows, max(0, tip - half) : tip]
    right = yellow[rows, tip + 1 : tip + 1 + half][:, ::-1]
    k = min(left.shape[1], right.shape[1])
    left, right = left[:, left.shape[1] - k :], right[:, right.shape[1] - k :]
    return float((left & right).sum() / max(1, int((left | right).sum())))


def _classify(band: _Band, row: _Row, tip: float, p: float) -> str:
    """Awakened (dark centre, pink lower half), plain (bright yellow centre, no pink) or unknown."""
    height, width = band.shape
    cy = row.top + CENTRE_DEPTH * p
    radius = max(1.0, CENTRE_RADIUS * p)
    ys, xs = np.ogrid[:height, :width]
    disk = (xs - tip) ** 2 + (ys - cy) ** 2 <= radius * radius
    columns = slice(max(0, int(tip - REFERENCE_HALF_WIDTH * p)), int(tip + REFERENCE_HALF_WIDTH * p) + 1)
    own_yellow = band.v[:, columns][row.parts[:, columns]]
    reference = float(np.median(own_yellow)) if own_yellow.size else 255.0
    darkness = float(np.median(band.v[disk])) / reference if disk.any() else 1.0
    centre_hue = int(np.median(band.h[disk])) if disk.any() else -1
    lower = (
        slice(int(row.top + LOWER_ROWS[0] * p), int(min(height, row.top + LOWER_ROWS[1] * p))),
        slice(int(max(0, tip - LOWER_HALF_WIDTH * p)), int(min(width, tip + LOWER_HALF_WIDTH * p))),
    )
    hue, sat, val = band.h[lower], band.s[lower], band.v[lower]
    pink_px = (hue >= PINK_HUE[0]) & (hue <= PINK_HUE[1]) & (sat > PINK_S) & (val > PINK_V)
    pink = float(pink_px.mean()) if hue.size else 0.0
    if darkness <= AWAKENED_DARK_MAX and pink >= AWAKENED_PINK_MIN:
        return AWAKENED
    if darkness >= PLAIN_BRIGHT_MIN and pink <= PLAIN_PINK_MAX and YELLOW_HUE[0] <= centre_hue <= YELLOW_HUE[1]:
        return PLAIN
    return UNKNOWN


def _awakened_count(per_star: tuple[str, ...], warnings: list[str]) -> int | None:
    if UNKNOWN in per_star:
        warnings.append("some stars are neither clearly awakened nor plain: awakening not read")
        return None
    first_plain = per_star.index(PLAIN) if PLAIN in per_star else len(per_star)
    if AWAKENED in per_star[first_plain:]:
        warnings.append("an awakened star follows a plain one (assumed impossible: awakening fills from the left)")
    return per_star.count(AWAKENED)


def _cross_checks(stars: int, level_cap: int | None) -> list[str]:
    """MECH-HERO-01 (community): level cap = stars x 10. A mismatch is a warning, never a correction.
    (MECH-HERO-02, awakened <= stars, holds by construction: each star is classified once.)"""
    if level_cap is None:
        return []
    if level_cap % 10 or level_cap // 10 != stars:
        return [f"{stars} star(s) but level cap {level_cap} (MECH-HERO-01: cap = stars x 10)"]
    return []
