"""Classify the stat icon next to each gear value of the Hero Info screen (M7), from the user's own capture.

The gear panel shows every value (main stat and up to four substats per piece, MECH-GEAR-01 / MECH-GEAR-03; the
Exclusive Equipment stat, MECH-EE-01) with a stat icon on its left; the text alone cannot tell ATK from HP or DEF.
Evidence (user captures 2026-10-04, Stove PC client, English: 5 Hero Info captures with gear, each at UI scales
0.64 / 1.0 / 1.28; prototype `spikes/m7_stat_icons.py`, method "grad2"):
- the gear icons are the same artwork as the nine icons left of the stat labels of the stats panel (MECH-STAT-01:
  Attack ... Dual Attack Chance), so the templates are cut from the same capture at run time: no game asset is stored
  and no resolution is assumed;
- relative to that label icon, substat icons are 0.85-0.95x, main-stat icons 0.95-1.0x and the EE icon about 0.65x;
  substat icons are semi-transparent grey, main-stat icons opaque white, over backgrounds from dark red to a bright
  pink/white nebula;
- result: 149/149 icons correct, 0 wrong, 0 abstained at each scale; blank windows (an empty 4th substat slot, the
  gaps between pieces) and icons whose template was removed were never accepted. On degraded copies (JPEG 60,
  noise, darker, washed out, blur; 2233 icons) 2222 correct, 0 wrong, 11 abstained.

Method:
1. templates: left of each stat label, Otsu on min(B, G, R) isolates the grey icon on the dark stats panel;
2. feature: the colour gradient as a doubled-angle field (gx^2 - gy^2, 2 gx gy) / |g|, which ignores contrast
   polarity: a grey icon brighter than dark red and one darker than a white nebula give the same field;
3. normalised cross-correlation (`cv2.TM_CCORR_NORMED`) of every template over shifts and scales;
4. per group of windows (one gear column and kind, or the EE alone): the icon column x is the median best-match x
   of the group's windows, the icon size the median best scale (two-pass consensus);
5. margin rule: a family is accepted only when its score is at least MIN_SCORE and beats the runner-up by
   MIN_MARGIN; otherwise the reading abstains (family None) and the caller asks for review. Dual Attack Chance is
   not a gear stat but is never remapped: if its icon wins, it is reported as such.

Lengths are in label row pitches (pL, the distance between two stat labels: 23.5 / 37 / 47 px at UI scales
0.64 / 1.0 / 1.28) or in template sizes; no pixel position is hard-coded.
Limits: one client (Stove PC, English) and aspect ratio seen; English labels are needed to find the templates (SPEC
D39). The weakest case is a semi-transparent substat icon on the bright nebula at the smallest UI scale (lowest correct
margin 0.160, just above MIN_MARGIN). The heart (HP) and the shield (DEF) are lookalikes: with one of the two
templates missing, the other is accepted on degraded copies, which is why `build_templates` fails closed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from statistics import median
from types import MappingProxyType
from typing import Final

import cv2
import numpy as np
import numpy.typing as npt

from e7ac.domain.codes import Stat
from e7ac.vision.ocr import Box

type BgrImage = npt.NDArray[np.uint8]
type GradientField = npt.NDArray[np.float32]

ICON_STATS: Final = (
    Stat.ATK,
    Stat.DEF,
    Stat.HP,
    Stat.SPEED,
    Stat.CRIT_CHANCE,
    Stat.CRIT_DAMAGE,
    Stat.EFFECTIVENESS,
    Stat.EFFECT_RESISTANCE,
    Stat.DUAL_ATTACK,
)
"""The nine stat-label icons = the icon families, in the stats panel's top-to-bottom order."""

# --- cutting the templates (lengths in label row pitches pL) ---
ICON_REGION_LEFT: Final = 1.6
ICON_REGION_RIGHT: Final = 0.1
"""The label icon lies in x [label left - 1.6 pL, label left - 0.1 pL] ..."""
ICON_REGION_HALF_HEIGHT: Final = 0.6
"""... and within the row centre +- 0.6 pL (rows are 1 pL apart; the icons measured 0.62-0.75 pL tall)."""
MIN_COMPONENT_AREA: Final = 0.004
"""Icon parts smaller than this (in pL^2) are specks, not icon strokes."""
MAX_COMPONENT_OFFSET: Final = 0.38
"""An icon part's centre is within this many pL of the row centre; parts of the neighbouring rows are further."""
TEMPLATE_PAD: Final = 0.15
"""Background kept around the icon's bounding box, per side, as a fraction of the box's longer side."""
TEMPLATE_SIZE_RANGE: Final = (0.6, 1.2)
"""A padded template's longer side in pL: measured 0.81-0.99 on 6 captures x 3 scales. Outside = not an icon."""
LABEL_GAP_TOLERANCE: Final = 0.25
"""Each gap between consecutive labels must be within pitch x (1 +- this): measured 0.905-1.061 (OCR box jitter)."""

# --- matching ---
GRADIENT_BLUR: Final = 0.0215
"""Gaussian blur before the gradient, in pL (= 0.025 substat row pitches in the prototype; never below 0.5 px)."""
MIN_BLUR_PX: Final = 0.5
SHIFT: Final = 0.13
"""Search half-range around (icon column x, window centre y), in pL (= 0.15 substat pitches): a matched icon was
measured at most 0.05 substat pitches off in x and 0.09 in y."""
SEARCH_SCALES: Final = tuple(round(0.70 + 0.05 * step, 2) for step in range(13))
"""Gear icon size / label icon size tried in the first pass (0.70-1.30): measured 0.85-0.95 for substats,
0.95-1.0 for main stats."""
CONSENSUS_SPREAD: Final = 0.05
"""Second pass: only the group's median best scale +- this."""
MIN_WINDOW_WIDTH: Final = 1.2
"""A window narrower than this many template sizes (cut by the digits) does not vote for the icon column."""
MIN_SCORE: Final = 0.45
"""Lowest accepted NCC. Measured on 5 captures x 3 scales: blank windows (21 per scale) at most 0.355, correct
matches at least 0.515 (the lowest are semi-transparent substat icons on the bright nebula; main stats >= 0.85)."""
MIN_MARGIN: Final = 0.15
"""Lowest accepted best - runner-up. Measured: blank windows at most 0.103; correct matches at least 0.160; icons
whose true template was removed reached 0.147 with a score >= MIN_SCORE (0 of 447 accepted)."""


_NO_GRADIENT: Final = 1e-6
"""Added to the gradient magnitude so flat areas give a zero field instead of 0 / 0."""


class IconError(Exception):
    """The stat-icon templates cannot be built from this capture (the matching would not be trustworthy)."""


@dataclass(frozen=True, slots=True, eq=False)
class StatTemplates:
    """The icons left of the stat labels, cut from ONE capture by `build_templates` (never stored: game artwork).

    `build_templates` always cuts all nine families or fails (a missing family would make its icons look like their
    nearest impostor); a smaller set is only meant for negative controls in tests."""

    crops: Mapping[Stat, BgrImage]
    """BGR icon crops (bounding box plus TEMPLATE_PAD), in ICON_STATS order."""
    pitch: float
    """Label row pitch in pixels: the length unit of the matching (blur, shift)."""

    def __post_init__(self) -> None:
        unknown = [str(stat) for stat in self.crops if stat not in ICON_STATS]
        if unknown or len(self.crops) < 2:
            raise IconError(
                f"templates need at least two stat-icon families (got {len(self.crops)}, unknown {unknown})"
            )
        if not self.pitch > 0:
            raise IconError(f"label pitch must be positive (got {self.pitch})")
        for stat, crop in self.crops.items():
            if crop.dtype != np.uint8 or crop.ndim != 3 or crop.shape[2] != 3 or min(crop.shape[:2]) < 3:
                raise IconError(f"template {stat.name} is not a BGR uint8 image of at least 3x3 pixels")

    @property
    def size(self) -> float:
        """Median longer side of the templates, in pixels."""
        return float(median(max(crop.shape[:2]) for crop in self.crops.values()))


@dataclass(frozen=True, slots=True)
class IconWindow:
    """Where to look for one icon, supplied by the gear-panel reader.

    `box` is generous: roughly [value right - 4.6 p, min(value left, value right - 1.5 p)] x (row centre +- 0.75 p),
    p = substat row pitch (measured icon centre: value right - 3.1 p); it must hold the whole icon and should stop
    before the digits. An OCR line box can start at the icon itself ("2,765" and the EE "12%" were read so), hence
    the min. The box's vertical centre is taken as the icon's centre.
    Windows of one `group` share the icon column and the icon size (e.g. "L.main", "L.sub", "R.main", "R.sub",
    "ee"): never mix columns, or main-stat and substat rows, in one group."""

    box: Box
    group: str


@dataclass(frozen=True, slots=True)
class IconMatch:
    """The icon read in one window. `family` None = abstained by the margin rule (or nothing could be matched):
    the caller must not guess and asks for review. `Stat.DUAL_ATTACK` is reported as is (not a gear stat)."""

    family: Stat | None
    best: Stat
    score: float
    """NCC of the best family, 0.0 when no template could be matched in the window."""
    second: Stat
    margin: float
    """score - the runner-up's score."""
    box: Box | None
    """Where the best family matched (also when abstaining); None when nothing could be matched."""


def build_templates(image: BgrImage, labels: Mapping[Stat, Box]) -> StatTemplates:
    """Cut the nine stat icons left of the stat labels (`HeroScreenReading.anchors.labels`, OCR boxes).

    Raises IconError when a label is missing, the labels are not one evenly spaced column in the panel's order, or
    an icon cannot be cut: matching with an incomplete or wrong template set would not be trustworthy."""
    bgr = _as_bgr(image)
    missing = [stat.name for stat in ICON_STATS if stat not in labels]
    if missing:
        raise IconError(f"stat labels not found ({', '.join(missing)}): the icon templates need all nine")
    pitch = _label_pitch([labels[stat] for stat in ICON_STATS])
    left = float(median(labels[stat].x0 for stat in ICON_STATS))
    crops: dict[Stat, BgrImage] = {}
    for stat in ICON_STATS:
        crop = _cut_icon(bgr, left, labels[stat].cy, pitch)
        if crop is None:
            raise IconError(f"no icon found left of the {stat.name} label")
        size = max(crop.shape[:2]) / pitch
        low, high = TEMPLATE_SIZE_RANGE
        if not low <= size <= high:
            raise IconError(f"the {stat.name} icon is {size:.2f} label pitches wide, expected {low}-{high}")
        crops[stat] = crop
    return StatTemplates(crops=MappingProxyType(crops), pitch=pitch)


def match_icons(image: BgrImage, templates: StatTemplates, windows: Sequence[IconWindow]) -> list[IconMatch]:
    """Classify the icon in each window (same order as `windows`), with a consensus per group:
    the icon column x and the icon size are shared by the group's windows (a single-window group uses its own)."""
    bgr = _as_bgr(image)
    scorer = _Scorer(templates)
    groups: dict[str, list[int]] = {}
    for index, window in enumerate(windows):
        groups.setdefault(window.group, []).append(index)
    matches: dict[int, IconMatch] = {}
    for indices in groups.values():
        found = _match_group(bgr, scorer, [windows[i].box for i in indices])
        matches.update(zip(indices, found, strict=True))
    return [matches[index] for index in range(len(windows))]


# --------------------------------------------------------------------------------------------------- templates


def _as_bgr(image: BgrImage) -> BgrImage:
    array = np.asarray(image)
    if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
        raise IconError(f"expected a BGR uint8 image, got {array.dtype} {array.shape}")
    return array


def _label_pitch(rows: Sequence[Box]) -> float:
    """Median distance between consecutive labels, which must run top to bottom in ICON_STATS order, evenly."""
    gaps = [b.cy - a.cy for a, b in pairwise(rows)]
    if any(gap <= 0 for gap in gaps):
        raise IconError("the stat labels are not in the panel's top-to-bottom order")
    pitch = float(median(gaps))
    if any(abs(gap / pitch - 1) > LABEL_GAP_TOLERANCE for gap in gaps):
        raise IconError("the stat labels are not evenly spaced (not one column of rows)")
    return pitch


def _cut_icon(image: BgrImage, left: float, cy: float, pitch: float) -> BgrImage | None:
    """The grey icon left of one label: Otsu on min(B, G, R) of the search region, the components near the row
    centre, their bounding box plus TEMPLATE_PAD. None when nothing is found."""
    region_box = Box(
        left - ICON_REGION_LEFT * pitch,
        cy - ICON_REGION_HALF_HEIGHT * pitch,
        left - ICON_REGION_RIGHT * pitch,
        cy + ICON_REGION_HALF_HEIGHT * pitch,
    )
    region, (ox, oy) = _crop(image, region_box)
    if region.size == 0:
        return None
    darkest = np.ascontiguousarray(region.min(axis=2))
    whiteness = np.asarray(cv2.normalize(darkest, np.empty_like(darkest), 0, 255, cv2.NORM_MINMAX), np.uint8)
    _, mask = cv2.threshold(whiteness, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    count, components, stats, centroids = cv2.connectedComponentsWithStats(np.asarray(mask, np.uint8), connectivity=8)
    components, stats, centroids = np.asarray(components), np.asarray(stats), np.asarray(centroids)
    row_centre = cy - oy
    keep = [
        index
        for index in range(1, count)
        if stats[index, cv2.CC_STAT_AREA] >= MIN_COMPONENT_AREA * pitch * pitch
        and abs(centroids[index][1] - row_centre) <= MAX_COMPONENT_OFFSET * pitch
    ]
    if not keep:
        return None
    ys, xs = np.nonzero(np.isin(components, keep))
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    pad = round(TEMPLATE_PAD * max(x1 - x0, y1 - y0))
    crop, _ = _crop(image, Box(ox + x0 - pad, oy + y0 - pad, ox + x1 + pad, oy + y1 + pad))
    return crop.copy() if min(crop.shape[:2]) >= 3 else None


def _crop(image: BgrImage, box: Box) -> tuple[BgrImage, tuple[int, int]]:
    """The part of `box` inside the image (rounded to pixels) and its top-left corner."""
    height, width = image.shape[:2]
    x0, y0 = max(0, round(box.x0)), max(0, round(box.y0))
    x1, y1 = min(width, round(box.x1)), min(height, round(box.y1))
    return image[y0 : max(y0, y1), x0 : max(x0, x1)], (x0, y0)


def _gradient_field(bgr: BgrImage, sigma: float) -> GradientField:
    """Colour gradient (per pixel, the channel with the largest magnitude) as the doubled-angle field
    (gx^2 - gy^2, 2 gx gy) / |g|: magnitude kept, contrast polarity dropped."""
    blurred = cv2.GaussianBlur(bgr.astype(np.float32), (0, 0), sigma)
    gx = np.asarray(cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3), np.float32)
    gy = np.asarray(cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3), np.float32)
    strongest = (gx * gx + gy * gy).argmax(axis=2)[..., None]
    gx = np.take_along_axis(gx, strongest, axis=2)[..., 0]
    gy = np.take_along_axis(gy, strongest, axis=2)[..., 0]
    magnitude = np.sqrt(gx * gx + gy * gy) + _NO_GRADIENT
    return np.dstack([(gx * gx - gy * gy) / magnitude, 2 * gx * gy / magnitude]).astype(np.float32)


# --------------------------------------------------------------------------------------------------- matching


@dataclass(frozen=True, slots=True)
class _Hit:
    """Best placement of one family in a window; box None = the template fits nowhere in the window."""

    stat: Stat
    score: float
    box: Box | None
    scale: float


class _Scorer:
    """Scores every template over a window; template fields are computed once per (family, scale)."""

    def __init__(self, templates: StatTemplates) -> None:
        self._templates = templates
        self._sigma = max(MIN_BLUR_PX, GRADIENT_BLUR * templates.pitch)
        self._shift = SHIFT * templates.pitch
        self.size = templates.size
        self._fields: dict[tuple[Stat, float], GradientField] = {}

    def rank_at(self, image: BgrImage, x: float, y: float, scales: Sequence[float]) -> list[_Hit]:
        """Ranking in a square window centred on (x, y), just big enough for the largest scale plus the shift."""
        half = 0.5 * self.size * max(scales) + self._shift
        return self.rank(image, Box(x - half, y - half, x + half, y + half), scales)

    def rank(self, image: BgrImage, box: Box, scales: Sequence[float]) -> list[_Hit]:
        """Every family's best placement in `box`, best first (ties keep ICON_STATS order)."""
        window, origin = _crop(image, box)
        if min(window.shape[:2]) < 3:
            return [_Hit(stat, -1.0, None, 0.0) for stat in self._templates.crops]
        field = _gradient_field(window, self._sigma)
        hits = [self._best(stat, field, origin, scales) for stat in self._templates.crops]
        return sorted(hits, key=lambda hit: hit.score, reverse=True)

    def _best(self, stat: Stat, field: GradientField, origin: tuple[int, int], scales: Sequence[float]) -> _Hit:
        best = _Hit(stat, -1.0, None, 0.0)
        for scale in scales:
            template = self._field(stat, scale)
            th, tw = template.shape[:2]
            if th > field.shape[0] or tw > field.shape[1]:
                continue
            _, score, _, (lx, ly) = cv2.minMaxLoc(cv2.matchTemplate(field, template, cv2.TM_CCORR_NORMED))
            if math.isfinite(score) and score > best.score:
                x0, y0 = origin[0] + lx, origin[1] + ly
                best = _Hit(stat, score, Box(x0, y0, x0 + tw, y0 + th), scale)
        return best

    def _field(self, stat: Stat, scale: float) -> GradientField:
        key = (stat, round(scale, 4))
        if key not in self._fields:
            crop = self._templates.crops[stat]
            height, width = crop.shape[:2]
            size = (max(3, round(width * scale)), max(3, round(height * scale)))
            interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
            resized = np.asarray(cv2.resize(crop, size, interpolation=interpolation), np.uint8)
            self._fields[key] = _gradient_field(resized, self._sigma)
        return self._fields[key]


def _match_group(image: BgrImage, scorer: _Scorer, boxes: Sequence[Box]) -> list[IconMatch]:
    """Pass 1: each window's best match at any scale votes for the icon column x and the icon size; pass 2: every
    window is re-read at (column x, its centre y), only around the voted scale."""
    first = [scorer.rank(image, box, SEARCH_SCALES) for box in boxes]
    votes = [ranking[0] for box, ranking in zip(boxes, first, strict=True) if _votes(image, box, ranking, scorer.size)]
    if not votes:  # no confident icon in the whole group: the first pass decides (the margin rule abstains)
        return [_decide(ranking) for ranking in first]
    column = float(median((hit.box.x0 + hit.box.x1) / 2 for hit in votes if hit.box is not None))
    scale = float(median(hit.scale for hit in votes))
    scales = (scale - CONSENSUS_SPREAD, scale, scale + CONSENSUS_SPREAD)
    return [_decide(scorer.rank_at(image, column, box.cy, scales)) for box in boxes]


def _votes(image: BgrImage, box: Box, ranking: Sequence[_Hit], template_size: float) -> bool:
    """A window votes when its ranking is complete, its best match confident and it was not cut by the digits."""
    window, _ = _crop(image, box)
    return _complete(ranking) and ranking[0].score >= MIN_SCORE and window.shape[1] >= MIN_WINDOW_WIDTH * template_size


def _complete(ranking: Sequence[_Hit]) -> bool:
    """Every family could be placed in the window (otherwise the ranking compares nothing with something)."""
    return all(hit.box is not None for hit in ranking)


def _decide(ranking: Sequence[_Hit]) -> IconMatch:
    """The margin rule. A ranking where some family fits nowhere is incomplete: it is never accepted."""
    best, second = ranking[0], ranking[1]
    if not _complete(ranking):
        return IconMatch(None, best.stat, 0.0, second.stat, 0.0, None)
    margin = best.score - second.score
    accepted = best.score >= MIN_SCORE and margin >= MIN_MARGIN
    return IconMatch(best.stat if accepted else None, best.stat, best.score, second.stat, margin, best.box)
