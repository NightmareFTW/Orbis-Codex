"""Read gear SET icons on the Hero Info screen (M7): the set badge of each gear piece and the active-set icons.

Every gear piece belongs to one set (MECH-GEAR-05, piece counts `community`); the Hero Info screen shows it only as
a small badge at the bottom-right of the item icon, and the completed sets as round icons right of the CP number.
The Equipment tab already prints the active set NAMES as text (`hero_screen._read_sets`), so this module is for
Hero Info only.

Evidence (user captures 2026-10-04, Stove PC client, English: 5 Hero Info captures with gear plus one without, each
at UI scales 0.64 / 1.0 / 1.28; prototype `spikes/m7_sets.py`):
- the in-game badges are the same artwork as the official Stove "wearingStatus" set icons (`sources.assets`): a
  shield with a gold rim and a gold glyph on a red or blue fill. Seen for 9 sets (Destruction, Health, Speed,
  Immunity, Hit, Rage, Critical, Pursuit, Penetration); assumed for the other 15;
- result: 90/90 piece badges and 30/30 active-set icons correct, 0 wrong, 0 review; the hero without gear gives no
  icon; 2160 synthetic insertions of all 24 icons over the real badges (blurred, noisy, re-encoded): 0 wrong.

Method:
1. references: the Stove icons, cropped to their alpha box; their fill (red / blue) is measured on the icon itself;
2. locate: every set icon has the same gold rim, so a rim-only template (grey mean of all icons, ring mask) is
   searched with masked ZNCC over a range of heights relative to the anchor (the item icon box or the CP line);
3. classify: the badge is compared with every reference icon in grey levels (full shield mask, small scale and shift
   search), which makes the glyph comparison independent of the fill colour;
4. decide (golden rule, never a silent near-miss): accept the best set only when its score is at least SCORE_MIN,
   the fill colour measured inside the badge equals the best icon's fill (colour gate), and it beats the best OTHER
   set of the same fill by MARGIN_MIN (same-fill margin: the colour gate already rules out the other fill).
   Otherwise the reading is REVIEW (`set_code` None) with the scores kept for the caller.

Lengths are in item-icon sides (pieces) or CP line heights (active sets); no pixel position is hard-coded.
Limits: thresholds measured on 9 of 24 sets and one device; weak pairs in the synthetic test are Weakening / Hit
(same fill) and Defense / Penetration (told apart by the fill); badges below about 25 px drift towards REVIEW.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Final, Literal

import cv2
import numpy as np
import numpy.typing as npt

from e7ac.vision.ocr import Box

type BgrImage = npt.NDArray[np.uint8]
type GreyImage = npt.NDArray[np.float32]
type Mask = npt.NDArray[np.float32]
type Fill = Literal["red", "blue", "unknown"]

# --- decision thresholds (5 geared captures x 3 scales; prototype: also 2160 synthetic insertions of all 24 icons) ---
FRAME_MIN: Final = 0.62
"""Rim-template ZNCC for a badge candidate: real piece badges scored >= 0.73 and CP-row icons >= 0.77; the best
non-badge peak inside the search regions <= 0.35 (pieces) and <= 0.46 (CP row, the hero without gear included)."""
SCORE_MIN: Final = 0.70
"""Lowest accepted full-icon ZNCC of the best set: real badges 0.83-0.96; non-badge peaks forced through <= 0.50."""
MARGIN_MIN: Final = 0.12
"""Lowest accepted best - best other set of the same fill: real badges >= 0.24; weakest synthetic pairs 0.09-0.15
(Weakening / Hit, Defense / Penetration: they go to REVIEW, never to a wrong set)."""
FILL_DOMINANCE_MIN: Final = 0.80
"""The winning hue's share of the red + blue votes inside a badge (measured 1.00 on every real badge and on the 24
Stove icons); below it the fill is 'unknown' and the colour gate fails."""
FILL_COVERAGE_MIN: Final = 0.10
"""Red + blue votes as a share of the fill mask (measured 0.45-0.70 on real badges); below it the fill is 'unknown'
(an all-gold or grey patch)."""

# --- shield geometry (fractions of the icon width) ---
RING_ERODE: Final = 0.30
"""Rim template mask = shield mask minus the shield eroded by this: the rim and a band of fill inside it."""
FILL_ERODE: Final = 0.18
"""The fill is measured inside the shield eroded by this (the gold rim excluded)."""

# --- fill hue votes (OpenCV HSV: hue 0-179) on saturated, not-bright pixels (the gold glyph and rim are excluded) ---
FILL_SATURATION_MIN: Final = 90
FILL_VALUE_RANGE: Final = (35, 200)
RED_HUE_MAX: Final = 8
"""Red fill: hue <= 8 or >= RED_HUE_WRAP."""
RED_HUE_WRAP: Final = 165
BLUE_HUE_RANGE: Final = (100, 135)

# --- search ---
HEIGHT_STEP: Final = 0.025
"""Relative step between two searched badge heights (about 1 px at 40 px)."""
NMS_WINDOW: Final = 0.8
"""Peaks closer than this many template sizes to a stronger peak are the same badge."""
CLASSIFY_SCALES: Final = (0.92, 0.96, 1.0, 1.04, 1.08)
"""Reference heights tried around the located frame height (the rim fit is within +-5%)."""
CLASSIFY_PAD: Final = 0.12
"""Shift search when classifying: the crop extends this fraction of the frame size beyond the located frame."""
MISSING_PEAK_SCORE: Final = FRAME_MIN - 0.2
"""Stand-in score of an expected but missing peak when the badge heights are compared."""
ALPHA_OPAQUE: Final = 128
"""Reference icon pixels with alpha above this belong to the shield (masks); ALPHA_VISIBLE bounds the crop."""
ALPHA_VISIBLE: Final = 8
MIN_BADGE_PX: Final = 12
"""Resolution floor: below this the glyphs carry no information (the smallest real badge seen is 29 px)."""

# --- piece badge, relative to the item icon box (side s = mean of its width and height) ---
PIECE_REGION: Final = (0.40, 0.30, 0.40, 0.40)
"""Search region (left, top, right, bottom): x from box.x0 + 0.40 s to box.x1 + 0.40 s, y from box.y0 + 0.30 s to
box.y1 + 0.40 s. Measured badge: x from box.x0 + 0.73 s to box.x1 + 0.10 s, y from box.y0 + 0.63 s to
box.y1 + 0.07 s (it overlaps the frame). Still 90/90 with the box shifted by 0.2 s in any direction, scaled by
0.8-1.3, or covering the whole card with the score strip below the artwork."""
PIECE_HEIGHT: Final = (0.32, 0.58)
"""Badge height in item-icon sides: measured 0.43-0.45 (46 px for a 105 px icon on a 2000 px wide capture)."""

# --- active-set icons, relative to the CP line box (height h) ---
ACTIVE_REGION: Final = (0.0, 0.5, 5.5, 0.5)
"""Search region (left, top, right, bottom): x from cp.x1 to cp.x1 + 5.5 h, y from cp.y0 - 0.5 h to cp.y1 + 0.5 h.
Measured icons: x from cp.x1 + 1.5 h to cp.x1 + 3.9 h for two icons (a third one ends about 0.9 h further);
the stat value column ends 3.9-5.0 h right of cp.x1."""
ACTIVE_HEIGHT: Final = (0.5, 1.0)
"""Icon height in CP line heights: measured 0.66-0.77 (the OCR box height of the CP number varies by about 10%)."""
ACTIVE_MAX: Final = 3
"""Six pieces complete at most three sets (three 2-piece sets, MECH-GEAR-05)."""


@dataclass(frozen=True, slots=True)
class SetMatch:
    """One set icon read from the screen. `set_code` None = REVIEW (margin, score or colour rule not met)."""

    set_code: str | None
    best: str
    """Best-scoring set code ('' when no badge was located)."""
    score: float
    """Grey ZNCC of `best` (0..1)."""
    second: str
    """The runner-up the margin is measured against: the best other set of the observed fill when the colour gate
    passed, else the best other set overall."""
    margin: float
    box: Box | None
    """Where the icon was found; None when no badge frame was located (a piece always has a set: REVIEW)."""
    fill: Fill = "unknown"
    """Fill colour measured inside the badge."""

    @property
    def confidence(self) -> float:
        """0 for REVIEW; otherwise grows with the score and the margin above their thresholds (1 = clear-cut).

        A ranking aid for review screens, not a probability (real badges: 0.43-0.87)."""
        if self.set_code is None:
            return 0.0
        by_score = min(1.0, (self.score - SCORE_MIN) / (1.0 - SCORE_MIN))
        by_margin = min(1.0, self.margin / (2 * MARGIN_MIN))
        return round(max(0.0, by_score) * by_margin, 3)


@dataclass(frozen=True, slots=True)
class _Reference:
    grey: GreyImage
    mask: Mask
    fill: Fill


@dataclass(frozen=True, slots=True)
class _Frame:
    """A located badge frame: rim-template score and box in image pixels (integers: array indices)."""

    score: float
    x: int
    y: int
    w: int
    h: int

    @property
    def box(self) -> Box:
        return Box(float(self.x), float(self.y), float(self.x + self.w), float(self.y + self.h))

    def padded(self, fraction: float) -> tuple[slice, slice]:
        """Row and column slices of the frame grown by `fraction` of its size on every side."""
        pad_x, pad_y = math.ceil(fraction * self.w), math.ceil(fraction * self.h)
        rows = slice(max(0, self.y - pad_y), self.y + self.h + pad_y)
        columns = slice(max(0, self.x - pad_x), self.x + self.w + pad_x)
        return rows, columns


class SetIconError(ValueError):
    """The reference icons cannot be used (not a PNG with transparency, or fewer than two sets)."""


class SetIconMatcher:
    """Locate and classify set icons against reference icons (the Stove set icons, keyed by catalog set code).

    Build it with `from_png`. The matcher only knows the sets it was given: callers check that every catalog set has
    an icon (a set without one could be read as its nearest look-alike) and say so (`e7 doctor`, `e7 catalog sync`)."""

    def __init__(self, references: Mapping[str, _Reference], rim: GreyImage, rim_mask: Mask, fill_mask: Mask) -> None:
        if len(references) < 2:
            raise SetIconError("at least two set icons are needed (the margin rule needs a runner-up)")
        self._references = dict(references)
        self._rim = rim
        self._rim_mask = rim_mask
        self._fill_mask = fill_mask.astype(np.uint8)
        self._resized: dict[tuple[str, int], tuple[GreyImage, Mask]] = {}

    @classmethod
    def from_png(cls, icons: Mapping[str, bytes]) -> SetIconMatcher:
        """Build from PNG bytes with an alpha channel, keyed by set code (`sources.assets.load_set_icons`)."""
        cropped = {code: _decode_icon(code, data) for code, data in icons.items()}
        if len(cropped) < 2:
            raise SetIconError("at least two set icons are needed (the margin rule needs a runner-up)")
        # one common size, so the rim template can be the mean of all icons
        height = int(np.median([icon.shape[0] for icon in cropped.values()]))
        width = int(np.median([icon.shape[1] for icon in cropped.values()]))
        resized = {code: _resized(icon, (width, height)) for code, icon in cropped.items()}
        shield = np.median(np.stack([icon[..., 3] for icon in resized.values()]), axis=0) > ALPHA_OPAQUE
        rim = np.stack([_grey(icon) for icon in resized.values()]).mean(axis=0).astype(np.float32)
        rim_mask = (shield & ~_eroded(shield, RING_ERODE)).astype(np.float32)
        references = {code: _reference(icon) for code, icon in resized.items()}
        return cls(references, rim, rim_mask, _eroded(shield, FILL_ERODE).astype(np.float32))

    @property
    def codes(self) -> frozenset[str]:
        """The set codes this matcher can recognise."""
        return frozenset(self._references)

    def reference_fill(self, code: str) -> Fill:
        """Fill colour measured on the reference icon of `code` ('unknown' = that set is never accepted)."""
        return self._references[code].fill

    def piece_set(self, image: BgrImage, item_icon: Box) -> SetMatch:
        """The set of one gear piece: the badge at the bottom-right of its item icon.

        `item_icon` is the square item artwork as located by the gear-panel reader (the score strip below it may be
        included). No badge found gives `box` None and `set_code` None: every piece has a set, so that is a REVIEW."""
        side = ((item_icon.x1 - item_icon.x0) + item_icon.height) / 2
        left, top, right, bottom = PIECE_REGION
        region = Box(
            item_icon.x0 + left * side,
            item_icon.y0 + top * side,
            item_icon.x1 + right * side,
            item_icon.y1 + bottom * side,
        )
        frames = self._locate(image, region, (PIECE_HEIGHT[0] * side, PIECE_HEIGHT[1] * side), expected=1)
        if not frames:
            return SetMatch(None, "", 0.0, "", 0.0, None)
        return self._classify(image, frames[0])

    def active_sets(self, image: BgrImage, cp: Box) -> list[SetMatch]:
        """The completed-set icons right of the CP number (`HeroScreenReading.anchors.cp`), in screen order (left to
        right); [] when there is none (a hero without gear). An icon that cannot be told is kept as a REVIEW."""
        h = cp.height
        left, top, right, bottom = ACTIVE_REGION
        region = Box(cp.x1 + left * h, cp.y0 - top * h, cp.x1 + right * h, cp.y1 + bottom * h)
        frames = self._locate(image, region, (ACTIVE_HEIGHT[0] * h, ACTIVE_HEIGHT[1] * h), expected=ACTIVE_MAX)
        return [self._classify(image, frame) for frame in sorted(frames, key=lambda f: f.x)]

    # ------------------------------------------------------------------ locate

    def _locate(self, image: BgrImage, region: Box, heights: tuple[float, float], *, expected: int) -> list[_Frame]:
        """Badge frames in `region`: the height whose `expected` best peaks score highest, then its peaks."""
        x0, y0, x1, y1 = _clip(region, image.shape)
        if x1 <= x0 or y1 <= y0:
            return []  # the region lies outside the image
        sub = _grey(image[y0:y1, x0:x1])
        best_quality, best_frames = -math.inf, []
        for height in _heights(*heights):
            template, mask = _scaled(self._rim, self._rim_mask, height)
            rows, columns = template.shape
            peaks = _peaks(_match(sub, template, mask), (rows, columns), expected)
            frames = [_Frame(score, x + x0, y + y0, columns, rows) for score, x, y in peaks]
            # a missing peak counts as a score below the gate, so the height that finds more badges wins
            scores = [f.score for f in frames] + [MISSING_PEAK_SCORE] * (expected - len(frames))
            quality = float(np.mean(scores))
            if quality > best_quality:
                best_quality, best_frames = quality, frames
        return best_frames

    # ------------------------------------------------------------------ classify

    def _classify(self, image: BgrImage, frame: _Frame) -> SetMatch:
        crop = _grey(image[frame.padded(CLASSIFY_PAD)])
        ranking = sorted(
            ((code, self._score(crop, code, frame.h)) for code in self._references), key=lambda item: -item[1]
        )
        best, score = ranking[0]
        badge = np.ascontiguousarray(image[frame.y : frame.y + frame.h, frame.x : frame.x + frame.w])
        mask = cv2.resize(self._fill_mask, (frame.w, frame.h), interpolation=cv2.INTER_NEAREST).astype(bool)
        fill, _ = _measure_fill(badge, mask)
        colour_ok = fill != "unknown" and fill == self._references[best].fill
        # same-fill margin: once the colour gate passed, sets of the other fill are no longer candidates
        rivals = [(code, s) for code, s in ranking[1:] if not colour_ok or self._references[code].fill == fill]
        second, second_score = rivals[0] if rivals else ("", 0.0)
        margin = score - second_score
        accepted = colour_ok and score >= SCORE_MIN and margin >= MARGIN_MIN
        return SetMatch(best if accepted else None, best, round(score, 4), second, round(margin, 4), frame.box, fill)

    def _score(self, crop: GreyImage, code: str, height: int) -> float:
        best = -1.0
        for factor in CLASSIFY_SCALES:
            template, mask = self._reference_at(code, round(height * factor))
            best = max(best, float(_match(crop, template, mask).max()))
        return best

    def _reference_at(self, code: str, height: int) -> tuple[GreyImage, Mask]:
        key = (code, height)
        if key not in self._resized:
            reference = self._references[code]
            self._resized[key] = _scaled(reference.grey, reference.mask, height)
        return self._resized[key]


# ---------------------------------------------------------------------- image helpers


def _reference(icon: npt.NDArray[np.uint8]) -> _Reference:
    shield = icon[..., 3] > ALPHA_OPAQUE
    fill, _ = _measure_fill(np.ascontiguousarray(icon[..., :3]), _eroded(shield, FILL_ERODE))
    return _Reference(grey=_grey(icon), mask=shield.astype(np.float32), fill=fill)


def _decode_icon(code: str, data: bytes) -> npt.NDArray[np.uint8]:
    """BGRA icon cropped to its alpha bounding box."""
    icon = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if icon is None or icon.ndim != 3 or icon.shape[2] != 4:
        raise SetIconError(f"set icon {code}: not a PNG with an alpha channel")
    if icon.dtype != np.uint8:  # 16-bit PNG
        icon = (icon // 257).astype(np.uint8)
    ys, xs = np.nonzero(icon[..., 3] > ALPHA_VISIBLE)
    if ys.size == 0:
        raise SetIconError(f"set icon {code}: fully transparent")
    return np.ascontiguousarray(icon[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1])


def _resized(icon: npt.NDArray[np.uint8], size: tuple[int, int]) -> npt.NDArray[np.uint8]:
    return np.asarray(cv2.resize(icon, size, interpolation=cv2.INTER_AREA), dtype=np.uint8)


def _grey(image: npt.NDArray[np.uint8]) -> GreyImage:
    bgr = image[..., :3] if image.ndim == 3 else image
    if bgr.ndim == 2:
        return bgr.astype(np.float32)
    return cv2.cvtColor(np.ascontiguousarray(bgr), cv2.COLOR_BGR2GRAY).astype(np.float32)


def _eroded(mask: npt.NDArray[np.bool_], fraction: float) -> npt.NDArray[np.bool_]:
    kernel = max(1, round(fraction * mask.shape[1]))
    eroded = cv2.erode(mask.astype(np.uint8), np.ones((kernel, kernel), np.uint8))
    return eroded.astype(bool)


def _measure_fill(bgr: BgrImage, mask: npt.NDArray[np.bool_]) -> tuple[Fill, float]:
    """Red vs blue fill by hue votes over saturated, not-bright pixels inside `mask` -> (fill, dominance)."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    low, high = FILL_VALUE_RANGE
    selected = mask & (saturation > FILL_SATURATION_MIN) & (value > low) & (value < high)
    red = int((selected & ((hue <= RED_HUE_MAX) | (hue >= RED_HUE_WRAP))).sum())
    blue = int((selected & (hue > BLUE_HUE_RANGE[0]) & (hue < BLUE_HUE_RANGE[1])).sum())
    votes = red + blue
    area = int(mask.sum())
    if votes == 0 or area == 0 or votes < FILL_COVERAGE_MIN * area:
        return "unknown", 0.0
    dominance = max(red, blue) / votes
    if dominance < FILL_DOMINANCE_MIN:
        return "unknown", dominance
    return ("red" if red > blue else "blue"), dominance


def _scaled(grey: GreyImage, mask: Mask, height: float) -> tuple[GreyImage, Mask]:
    factor = height / grey.shape[0]
    width = max(1, round(grey.shape[1] * factor))
    rows = max(1, round(height))
    template = cv2.resize(grey, (width, rows), interpolation=cv2.INTER_AREA)
    scaled_mask = cv2.resize(mask, (width, rows), interpolation=cv2.INTER_AREA)
    return template.astype(np.float32), (scaled_mask > 0.5).astype(np.float32)


def _match(image: GreyImage, template: GreyImage, mask: Mask) -> GreyImage:
    """Masked ZNCC map; -1 where it is undefined (flat patches) or when the template does not fit."""
    if image.shape[0] < template.shape[0] or image.shape[1] < template.shape[1]:
        return np.full((1, 1), -1.0, np.float32)
    result = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED, mask=mask)
    result[~np.isfinite(result)] = -1.0
    return np.clip(result, -1.0, 1.0).astype(np.float32)


def _peaks(response: GreyImage, size: tuple[int, ...], expected: int) -> list[tuple[float, int, int]]:
    """Up to `expected` peaks >= FRAME_MIN, strongest first, each suppressing its NMS_WINDOW neighbourhood."""
    work = response.copy()
    height, width = size[0], size[1]
    found: list[tuple[float, int, int]] = []
    while len(found) < expected:
        y, x = np.unravel_index(int(work.argmax()), work.shape)
        score = float(work[y, x])
        if score < FRAME_MIN:
            break
        found.append((score, int(x), int(y)))
        dx, dy = round(NMS_WINDOW * width), round(NMS_WINDOW * height)
        work[max(0, y - dy) : y + dy + 1, max(0, x - dx) : x + dx + 1] = -math.inf
    return found


def _heights(low: float, high: float) -> Iterator[float]:
    """Badge heights from `low` to `high` in HEIGHT_STEP relative steps (none below MIN_BADGE_PX)."""
    height = max(low, MIN_BADGE_PX)
    while height <= high:
        yield height
        height *= 1 + HEIGHT_STEP


def _clip(region: Box, shape: tuple[int, ...]) -> tuple[int, int, int, int]:
    x0 = min(max(0, math.floor(region.x0)), shape[1])
    y0 = min(max(0, math.floor(region.y0)), shape[0])
    x1 = min(max(x0, math.ceil(region.x1)), shape[1])
    y1 = min(max(y0, math.ceil(region.y1)), shape[0])
    return x0, y0, x1, y1
