"""Read the Memory Imprint icon of a hero screen (M7): self or team mode, lit squares, grade letters, "Locked".

MECH-IMP-02 (verified): the Equipment tab and Hero Info show the hero's ACTIVE imprint as an icon left of the imprint
text: a crosshair for self ("Imprint Concentration"), four rounded squares in a diamond for team ("Imprint Release";
only the heroes in the lit positions receive the bonus), grey squares and a padlock for "Locked". The grade letters
(B ... SSS) are drawn over the icon's bottom right, in the grade's colour.

Evidence: prototype `spikes/m7_imprint_stars.py` on the user's own captures (2026-10-04, Stove PC client, English):
6 Hero Info + 3 Equipment captures and 4 zoomed icon crops, at UI scales 0.64 / 1.0 / 1.28.
- Result: mode 13/13, lit squares 8/8, letter count and grade 12/12 at every scale, 0 wrong. Also 0 wrong at 0.50
  and 0.40, and on JPEG q50, blur, noise, darker and brighter copies (a few abstentions).
- Negative controls (same-size windows over the imprint text, above it and over the hero art; ~2400 windows tiled
  over the whole captures) read as unknown, except one "Locked" structure in a held-out window of an Equipment
  capture. That is why the OCR text "Locked" decides and the icon is only a consistency check (`locked_mismatch`).
- Measured, in relative units (lengths in imprint text line heights or in the icon's own half-width Rx and
  half-height Ry): team icon Rx/Ry 1.18-1.31, self 0.97-1.02; Ry = 0.89-1.10 text lines; unlit team squares keep the
  icon hue at 0.29-0.30 of the lit brightness (lit 0.95-1.00); grade-letter block width B 0.67-0.80 Ry, SSS
  1.48-1.78 Ry; letter colour = icon colour = imprint text colour on all 12 graded samples (blue OpenCV hue 101-108,
  red hue 0-1).

Method:
1. window: x from the level line's left edge to the imprint text, y from the level line to the CP line (all from
   `ScreenAnchors`); without a level line (a zoomed crop) a band around the imprint text block;
2. colour: dominant hue of the bright saturated pixels; too few of them -> the grey "Locked" path (padlock blob plus
   at least one grey square with a hole up-left of it);
3. icon mask: pixels of the icon hue, lit or dim (the dim squares and ring keep the hue at ~30 % brightness);
4. geometry from the parts the letters never cover: top row and left column give the centre (cx, cy), Rx and Ry;
   plausibility: aspect, size against the text line, mirror symmetry of the upper half;
5. mode: aspect Rx/Ry and the centre disk (filled for the crosshair, empty between the four squares) must agree, and
   a structure check (squares present, free corners empty / crosshair ring present) must pass; else unknown;
6. lit squares (team): median brightness of each square's visible part relative to the lit brightness;
7. grade letters: a band just below the icon footprint holds only letters; the letter count comes from the column
   profile (dark outlines between letters make valleys), checked against the block width and the number of blobs;
   the grade comes only from `GRADE_RULES`, the (colour, letter count) pairs seen on the captures. OCR is never used
   for the grade (it read 2-5 of 12 grades right in the prototype).

Limits: one client, layout and language seen. Only B (blue, 5 icons) and SSS (red, 7 icons) exist in the data; any
other grade reads as None with a warning. "Locked" has one zoomed sample. The crop with partly lit squares ("Health
+15%": right and bottom lit) is truth by eye only (provisional).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from statistics import median
from types import MappingProxyType
from typing import Final

import cv2
import numpy as np
import numpy.typing as npt

from e7ac.domain.codes import DataStatus
from e7ac.domain.roster import ImprintGrade, ImprintMode
from e7ac.vision.hero_screen import ScreenAnchors
from e7ac.vision.ocr import Box

type BgrImage = npt.NDArray[np.uint8]
type Channel = npt.NDArray[np.int32]
type Mask = npt.NDArray[np.bool_]

SQUARES: Final = ("top", "left", "right", "bottom")
"""Positions of the team icon's four squares (keys of `ImprintIconReading.lit`)."""
LIT: Final = "lit"
DARK: Final = "dark"
OCCLUDED: Final = "occluded"
UNKNOWN: Final = "unknown"

# --- window (lengths in level-line heights h, or in imprint text line heights when there is no level line) ---
LEVEL_LEFT_MARGIN: Final = 0.6
"""The window starts this many h left of the level line's left edge (the icon's left edge is 2.4-3.5 text lines left
of the imprint text, which starts 1-5 h right of the level line)."""
TEXT_GAP: Final = 0.05
"""The window ends this far left of the imprint text (letters may reach the text's left edge)."""
NO_CP_DEPTH: Final = 1.5
"""Without a CP line the window ends this many h below the imprint text."""
NO_LEVEL_WIDTH: Final = 5.0
"""Without a level line (zoomed crops): window width left of the text, in text lines (icon left edge measured
2.40-3.45 text lines left of the text)."""
NO_LEVEL_HALF_HEIGHT: Final = 2.6
"""Without a level line: half height around the text block centre, in text lines (icon height = 1.8-2.2 lines)."""
MIN_WINDOW_LINES: Final = 1.0
"""A window narrower or lower than one text line cannot hold the icon (about 2.6 x 2 lines)."""

# --- colour (OpenCV HSV: hue 0-179, saturation and value 0-255) ---
BRIGHT_S: Final = 100
BRIGHT_V: Final = 170
"""Clearly coloured pixels, used to find the icon hue (the darker / brighter copies of the prototype passed)."""
MIN_BRIGHT_FRACTION: Final = 0.01
"""Below this share of bright coloured pixels in the window there is no coloured icon: the grey (Locked) path."""
HUE_SMOOTH: Final = 4
"""Half width, in hue bins, of the circular smoothing before taking the dominant hue."""
RED_HUE_TOLERANCE: Final = 10
"""Red = within 10 of hue 0 (measured: icons 0-1, letters 0-1, text 0)."""
BLUE_HUE: Final = (95, 115)
"""Blue hue range (measured: icons 105-108, letters 105-108, text 101-104)."""
MIN_TEXT_COLOUR_FRACTION: Final = 0.03
"""Imprint text boxes: share of bright coloured pixels above which the text counts as coloured (measured 0.106-0.185
on coloured text, 0 on "Locked")."""

# --- icon mask (brightness relative to the icon's lit brightness V_lit = 95th percentile of its core) ---
HUE_TOLERANCE: Final = 6
"""Mask pixels are within this many hue bins of the icon hue."""
LIT_V: Final = 0.6
LIT_S: Final = 0.5
"""Lit parts: V >= 0.6 V_lit and S >= 0.5 x the core's median S (highlights are less saturated)."""
DIM_S: Final = 0.85
"""Dim parts keep the icon's saturation (>= 0.85 x median S); the red nebula behind red icons is less saturated."""
DIM_V_STEPS: Final = (0.24, 0.16, 0.11)
"""Dim parts: V >= this x V_lit. Unlit squares measure ~0.30 V_lit, the nebula behind the icon stays below ~0.22.
The lower steps are tried only when the icon found is implausible (a darker rendering loses the dim squares)."""
RETRY_CONFIDENCE: Final = 0.8
"""Mode confidence factor when the icon was only found with a lower dim step."""
CLUSTER_REACH: Final = 0.25
"""Components within this fraction of the largest component's size belong to the icon (squares are separate)."""
CLUSTER_MIN_AREA: Final = 0.01
"""Components smaller than this fraction of the largest are ignored."""
EDGE_BAND: Final = 0.06
"""The top (left) edge band used for cx (cy): this fraction of the mask's height (width), at least one pixel."""

# --- geometry and mode (Rx, Ry = the icon's half-width and half-height) ---
ASPECT_TEAM: Final = (1.14, 1.42)
"""Plausible Rx/Ry of the team icon (measured 1.18-1.31)."""
ASPECT_SELF: Final = (0.88, 1.12)
"""Plausible Rx/Ry of the self icon (measured 0.97-1.02)."""
SIZE_RANGE: Final = (0.7, 1.4)
"""Ry / imprint text line height (measured 0.89-1.10)."""
SYMMETRY_TOLERANCE: Final = 0.15
"""The upper half's right extent must mirror its left extent within 15 % of Rx (letters never reach that high)."""
SYMMETRY_ROWS: Final = (-0.3, 0.2)
"""Rows (Ry units around cy) used for the symmetry check."""
ASPECT_SPLIT: Final = 1.11
ASPECT_MARGIN: Final = 0.04
"""Team above the split, self below; confidence is full at 2 margins from the split (closest measured: 0.07)."""
CENTRE_RADIUS: Final = 0.1
"""Centre disk radius in Ry: lit pixels fill it for the crosshair (measured 1.00) and miss it for team (0.00)."""
CENTRE_SPLIT: Final = 0.5
CENTRE_MARGIN: Final = 0.3
STRUCTURE_ZONES: Final[Mapping[str, tuple[float, float, float]]] = MappingProxyType(
    {
        "sq_top": (0.0, -0.55, 0.15),
        "sq_left": (-0.64, 0.0, 0.15),
        "sq_right": (0.64, -0.1, 0.12),
        "c_tl": (-0.62, -0.62, 0.12),
        "c_tr": (0.62, -0.62, 0.12),
        "c_bl": (-0.62, 0.62, 0.12),
        "d_tl": (-0.5, -0.5, 0.1),
        "d_tr": (0.5, -0.5, 0.1),
        "d_bl": (-0.5, 0.5, 0.1),
        "ctr": (0.0, 0.0, 0.1),
    }
)
"""Structure zones (u, v, half size) in (Rx, Ry) units around the centre, only where letters never reach: square
bodies (sq_), box corners (c_), diagonals (d_) and the centre."""
TEAM_SQUARE_MIN: Final = 0.2
"""Team: mask coverage of the top, left and upper-right square bodies (measured 0.30-0.83, dim squares 0.30-0.46)."""
TEAM_CORNER_MAX: Final = 0.15
"""Team: the three free box corners (measured 0.00)."""
TEAM_CENTRE_MAX: Final = 0.35
"""Team: the centre (measured 0.00, up to 0.23 on JPEG q50 at scale 0.64)."""
SELF_RING_MIN: Final = 0.6
"""Self: the crosshair ring on the three free diagonals and the centre (measured 0.88-1.00)."""
SELF_CORNER_MAX: Final = 0.55
"""Self: the box corners (measured 0.11-0.42; an orange disk in hero art measured 0.69-0.86)."""

# --- lit squares (team) ---
SQUARE_CENTRES: Final[Mapping[str, tuple[float, float]]] = MappingProxyType(
    {"top": (0.0, -0.55), "left": (-0.64, 0.0), "right": (0.64, 0.0), "bottom": (0.0, 0.55)}
)
"""Square centres in (Rx, Ry) units."""
SQUARE_HALF: Final = (0.41, 0.39)
"""Half width (Rx) and half height (Ry) of one square."""
BOTTOM_SQUARE_CUT: Final = 0.15
"""Only the bottom square's part left of its centre - 0.15 Rx is read (the letters cover the rest); of the right
square only the upper half."""
LIT_MIN: Final = 0.70
DARK_MAX: Final = 0.50
"""Square level = median V of its mask pixels / V_lit: lit >= 0.70 (measured 0.95-1.00), dark <= 0.50 (0.29-0.30)."""
SQUARE_MIN_COVER: Final = 0.20
"""A square whose visible zone is less covered than this is occluded."""

# --- grade letters ---
LETTER_BAND: Final = (1.10, 1.75)
"""Rows of the letter band in Ry below cy: the icon footprint ends at 1.0 Ry, only letters are lower."""
LETTER_COLUMNS: Final = (-0.7, 2.4)
"""Columns of the letter band in Rx from cx."""
LETTER_FILL: Final = 0.75
"""Letter fill: band mask pixels with V >= 0.75 x the band's 90th percentile (keeps the dark outlines out)."""
LETTER_SPAN: Final = (0.02, 0.98)
"""The letter block is the column span holding the central 96 % of the fill pixels (ignores specks)."""
PROFILE_BLUR: Final = 0.02
"""Gaussian sigma of the column profile, in Ry (at least MIN_BLUR_PX)."""
MIN_BLUR_PX: Final = 0.6
PEAK_ENTER: Final = 0.55
PEAK_LEAVE: Final = 0.35
"""A letter starts where the profile rises above 0.55 x its peak and ends below 0.35 x (valleys: always 1 segment
for B and 3 for SSS)."""
LETTER_WIDTH_PER: Final = (0.42, 0.65, 0.35)
"""n letters are compatible with a block width in [0.42 n, 0.65 n + 0.35] Ry (B 0.67-0.80, SSS 1.48-1.78)."""
WIDTH_BINS: Final = ((0.0, 1.0, 1), (1.0, 1.4, 2), (1.4, 2.2, 3))
"""Letter count by block width alone (Ry); only a confidence vote (2 letters never seen)."""
MAX_LETTERS: Final = 3
BLOB_MIN_AREA: Final = 0.02
"""Letter blobs smaller than this (in Ry^2) are specks (blob count: 1 for B, 3 for SSS on every sample)."""
LETTER_COLOUR_V: Final = 0.6
"""Letter colour: band pixels with S >= BRIGHT_S and V >= 0.6 x the band's 99th percentile."""
LETTERS_AGREE_CONFIDENCE: Final = 0.9
LETTERS_WIDTH_ONLY_CONFIDENCE: Final = 0.7
NO_LETTERS_CONFIDENCE: Final = 0.3
TEXT_MISMATCH_FACTOR: Final = 0.5
"""Grade confidence factor when the imprint text colour differs from the icon colour."""

# --- Locked (one sample: translucent grey squares and a white padlock where the letters would be) ---
LOCK_WHITE_S: Final = 50
LOCK_WHITE_V: Final = 0.85
LOCK_CONTRAST: Final = 2.5
"""Padlock pixels: S <= 50 and V >= 0.85 x the window's 99.5th percentile V, which must be >= 2.5 x the median V."""
LOCK_ASPECT: Final = (1.05, 1.45)
"""Padlock height / width (measured 1.20-1.22)."""
LOCK_HEIGHT: Final = (0.7, 1.25)
"""Padlock height in text lines (measured 0.93-0.98)."""
LOCK_FILL: Final = (0.45, 0.75)
"""Padlock pixels / its bounding box (body and shackle)."""
GREY_S: Final = 0.6
GREY_V: Final = 1.6
"""Grey square pixels: S <= 0.6 x and V >= 1.6 x the window medians."""
GREY_ASPECT: Final = (1.1, 1.6)
"""Grey square width / height (measured 1.22-1.38)."""
GREY_HEIGHT: Final = (0.6, 1.2)
"""Grey square height in text lines (measured 0.82-0.95)."""
GREY_HOLE: Final = (0.02, 0.2)
"""Hole area / the square's bounding box."""
MIN_GREY_SQUARES: Final = 1
"""At scale 0.64 the right grey square merges with the padlock outline: only one is required."""
MIN_ZONE_PX: Final = 2
"""A measuring zone thinner than this many pixels cannot be measured."""


@dataclass(frozen=True, slots=True)
class GradeRule:
    """One observed (colour, letter count) -> grade pair, with its provenance (CLAUDE.md golden rules)."""

    grade: ImprintGrade
    source: str
    status: DataStatus


GRADE_RULES: Final[Mapping[tuple[str, int], GradeRule]] = MappingProxyType(
    {
        ("blue", 1): GradeRule(ImprintGrade.B, "user captures 2026-10-04", DataStatus.VERIFIED),
        ("red", 3): GradeRule(ImprintGrade.SSS, "user captures 2026-10-04", DataStatus.VERIFIED),
    }
)
"""The only grades the icon reader names: B (5 icons) and SSS (7 icons). Other grades and their colours have never
been seen: they read as None with a warning (MECHANICS "Needs verification")."""


@dataclass(frozen=True, slots=True)
class ImprintIconReading:
    mode: ImprintMode | None
    """None = unknown (no icon, Locked, or the features disagree)."""
    locked: bool
    """The window shows the Locked structure. Consistency check only: the OCR text "Locked" decides."""
    mode_confidence: float
    colour: str
    """"red" | "blue" | "grey" (no coloured icon in the window) | "other(h=NN)" (OpenCV hue) | "unknown" (no window)."""
    letters: int | None
    """Grade letters counted under the icon (0 = none found); None = unresolved."""
    grade: ImprintGrade | None
    grade_confidence: float
    lit: Mapping[str, str] | None
    """Team only: SQUARES -> "lit" / "dark" / "occluded" / "unknown"."""
    measurements: Mapping[str, float]
    warnings: tuple[str, ...]


def read_imprint_icon(image: BgrImage, anchors: ScreenAnchors) -> ImprintIconReading:
    """Read the imprint icon next to the imprint text found by `parse_hero_screen` (MECH-IMP-02)."""
    window = _icon_window(image.shape, anchors)
    if window is None:
        return _Trace().reading(
            colour=UNKNOWN, warning="no imprint icon window: the imprint text (or the level line) was not found"
        )
    x0, y0, x1, y1, text_height = window
    reading = read_icon_window(image[y0:y1, x0:x1], text_height)
    return _check_text_colour(reading, _text_colour(image, anchors.imprint))


def read_icon_window(window: BgrImage, text_height: float) -> ImprintIconReading:
    """Read an imprint icon inside `window` (BGR); `text_height` = height of one imprint text line in pixels."""
    trace = _Trace()
    if not text_height > 0 or min(window.shape[:2]) < MIN_WINDOW_LINES * text_height:
        return trace.reading(colour=UNKNOWN, warning="the icon window is smaller than one text line")
    hsv = _Hsv.of(window)
    bright = (hsv.s >= BRIGHT_S) & (hsv.v >= BRIGHT_V)
    if bright.mean() < MIN_BRIGHT_FRACTION:
        return _read_grey(hsv, text_height, trace)
    hue = _dominant_hue(hsv.h[bright])
    colour = colour_family(hue)
    trace.measure("icon_hue", hue)
    icon = _find_icon(hsv, bright, hue, text_height, trace)
    if icon is None:
        return trace.reading(colour=colour)
    mode, confidence = _decide_mode(icon, hsv, trace)
    if mode is None or not _structure_ok(mode, icon, trace):
        return trace.reading(colour=colour)
    lit = _lit_squares(icon, hsv, trace) if mode is ImprintMode.TEAM else None
    letters = _count_letters(icon, hsv, trace)
    grade, grade_confidence = _grade(colour, letters, trace)
    return ImprintIconReading(
        mode=mode,
        locked=False,
        mode_confidence=round(confidence, 3),
        colour=colour,
        letters=letters.count,
        grade=grade,
        grade_confidence=round(grade_confidence, 3),
        lit=lit,
        measurements=trace.frozen_measurements(),
        warnings=tuple(trace.warnings),
    )


def locked_mismatch(reading: ImprintIconReading, text_locked: bool) -> str | None:
    """Warning when the icon contradicts the OCR text: the text "Locked" decides (MECH-IMP-02), the icon only checks."""
    if text_locked and reading.mode is not None:
        return f"the text says 'Locked' but the icon reads as a {reading.mode.value} imprint"
    if text_locked and not reading.locked:
        return "the text says 'Locked' but the Locked icon was not found"
    if not text_locked and reading.locked:
        return "the icon looks Locked but the text shows an imprint"
    return None


def colour_family(hue: int) -> str:
    """Colour name of an OpenCV hue: only the two grade colours seen are named; any other hue is reported as is."""
    if min(hue % 180, 180 - hue % 180) <= RED_HUE_TOLERANCE:
        return "red"
    if BLUE_HUE[0] <= hue <= BLUE_HUE[1]:
        return "blue"
    return f"other(h={hue})"


# ------------------------------------------------------------------------------------------------ internals


@dataclass(slots=True)
class _Trace:
    """Measurements and warnings collected while reading one icon."""

    measurements: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def measure(self, key: str, value: float) -> None:
        self.measurements[key] = round(float(value), 3)

    def warn(self, text: str) -> None:
        self.warnings.append(text)

    def frozen_measurements(self) -> Mapping[str, float]:
        return MappingProxyType(dict(self.measurements))

    def reading(self, *, colour: str, warning: str | None = None, locked: bool = False) -> ImprintIconReading:
        """A reading without a mode (unknown or Locked)."""
        if warning is not None:
            self.warn(warning)
        return ImprintIconReading(
            mode=None,
            locked=locked,
            mode_confidence=0.0,
            colour=colour,
            letters=None,
            grade=None,
            grade_confidence=0.0,
            lit=None,
            measurements=self.frozen_measurements(),
            warnings=tuple(self.warnings),
        )


@dataclass(frozen=True, slots=True)
class _Hsv:
    h: Channel
    s: Channel
    v: Channel

    @classmethod
    def of(cls, image: BgrImage) -> _Hsv:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.int32)
        return cls(hsv[..., 0], hsv[..., 1], hsv[..., 2])


@dataclass(frozen=True, slots=True)
class _Icon:
    """The icon found in the window: its mask, centre and half sizes (pixels) and lit brightness."""

    mask: Mask
    cx: float
    cy: float
    rx: float
    ry: float
    v_lit: float
    dim_step: int

    def zone(self, u: tuple[float, float], v: tuple[float, float]) -> tuple[slice, slice] | None:
        """Pixel slices of the box u (Rx units from cx) x v (Ry units from cy), clipped to the window."""
        height, width = self.mask.shape
        x0 = max(0, round(self.cx + u[0] * self.rx))
        x1 = min(width, round(self.cx + u[1] * self.rx))
        y0 = max(0, round(self.cy + v[0] * self.ry))
        y1 = min(height, round(self.cy + v[1] * self.ry))
        if x1 - x0 < MIN_ZONE_PX or y1 - y0 < MIN_ZONE_PX:
            return None
        return slice(y0, y1), slice(x0, x1)

    def coverage(self, u: float, v: float, half: float) -> float:
        """Share of mask pixels in a square zone (-1 when the zone is outside the window)."""
        zone = self.zone((u - half, u + half), (v - half, v + half))
        return float(self.mask[zone].mean()) if zone is not None else -1.0


def _icon_window(shape: tuple[int, ...], anchors: ScreenAnchors) -> tuple[int, int, int, int, float] | None:
    """(x0, y0, x1, y1, text line height) of the region left of the imprint text that holds the icon."""
    boxes = anchors.imprint
    if not boxes:
        return None
    text_x0 = min(b.x0 for b in boxes)
    text_y0, text_y1 = min(b.y0 for b in boxes), max(b.y1 for b in boxes)
    text_height = float(median(b.height for b in boxes))
    if text_height <= 0:
        return None
    level, cp = anchors.level, anchors.cp
    if level is not None:
        unit = level.height
        x0, x1 = level.x0 - LEVEL_LEFT_MARGIN * unit, text_x0 - TEXT_GAP * unit
        y0 = level.y1
        y1 = cp.y0 if cp is not None else text_y1 + NO_CP_DEPTH * unit
    else:
        centre = (text_y0 + text_y1) / 2
        x0, x1 = text_x0 - NO_LEVEL_WIDTH * text_height, text_x0 - TEXT_GAP * text_height
        y0, y1 = centre - NO_LEVEL_HALF_HEIGHT * text_height, centre + NO_LEVEL_HALF_HEIGHT * text_height
    left, top = max(0, int(x0)), max(0, int(y0))
    right, bottom = min(shape[1], int(x1)), min(shape[0], int(y1))
    if min(right - left, bottom - top) < MIN_WINDOW_LINES * text_height:
        return None
    return left, top, right, bottom, text_height


def _hue_distance(hue: Channel, reference: int) -> Channel:
    distance: Channel = np.abs(hue - reference)
    folded: Channel = np.minimum(distance, 180 - distance)
    return folded


def _dominant_hue(hues: Channel) -> int:
    """Peak of the circular hue histogram after a +-HUE_SMOOTH box smoothing."""
    histogram = np.bincount(hues.ravel(), minlength=180)[:180]
    smooth = sum(np.roll(histogram, k) for k in range(-HUE_SMOOTH, HUE_SMOOTH + 1))
    return int(np.argmax(smooth))


def _find_icon(hsv: _Hsv, bright: Mask, hue: int, text_height: float, trace: _Trace) -> _Icon | None:
    """Mask the icon (lit and dim parts of its hue) and measure it; lower dim steps only when it is implausible."""
    same_hue = _hue_distance(hsv.h, hue) <= HUE_TOLERANCE
    core = bright & same_hue
    v_lit = float(np.percentile(hsv.v[core], 95))
    s_median = float(np.median(hsv.s[core]))
    lit_px = (hsv.v >= LIT_V * v_lit) & (hsv.s >= LIT_S * s_median)
    failures: list[str] = []
    for step, fraction in enumerate(DIM_V_STEPS):
        dim_px = (hsv.v >= fraction * v_lit) & (hsv.s >= DIM_S * s_median)
        mask = _main_cluster(same_hue & (lit_px | dim_px))
        icon, problem = _measure_geometry(mask, v_lit, step, text_height, trace)
        if icon is not None:
            if failures:
                trace.warn(f"icon found only with the dim threshold {fraction} V_lit ({'; '.join(failures)})")
                trace.measure("dim_fraction", fraction)
            return icon
        failures.append(f"{fraction} V_lit: {problem}")
    trace.warn(f"no plausible imprint icon: {failures[-1]}")
    return None


def _main_cluster(mask: Mask) -> Mask:
    """The largest component plus the components close to it (the squares are separate components). Components
    touching the window's top border are background: the window starts at the level line, the icon never does."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if count <= 1:
        return np.zeros(mask.shape, dtype=bool)
    areas = stats[:, cv2.CC_STAT_AREA].astype(np.int64)
    areas[0] = 0
    areas[stats[:, cv2.CC_STAT_TOP] == 0] = 0
    if areas.max() == 0:
        return np.zeros(mask.shape, dtype=bool)
    biggest = int(np.argmax(areas))
    keep = {biggest}
    x, y, w, h = (int(v) for v in stats[biggest, :4])
    gx0, gy0, gx1, gy1 = x, y, x + w, y + h
    reach = CLUSTER_REACH * max(w, h)
    changed = True
    while changed:
        changed = False
        for i in range(1, count):
            if i in keep or areas[i] == 0 or areas[i] < CLUSTER_MIN_AREA * areas[biggest]:
                continue
            x, y, w, h = (int(v) for v in stats[i, :4])
            if x < gx1 + reach and x + w > gx0 - reach and y < gy1 + reach and y + h > gy0 - reach:
                keep.add(i)
                gx0, gy0, gx1, gy1 = min(gx0, x), min(gy0, y), max(gx1, x + w), max(gy1, y + h)
                changed = True
    return np.isin(labels, list(keep))


def _measure_geometry(
    mask: Mask, v_lit: float, step: int, text_height: float, trace: _Trace
) -> tuple[_Icon | None, str]:
    """Centre and half sizes from the parts the letters never cover (top rows give cx, left columns cy)."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None, "no pixels of the icon colour"
    top, left = int(ys.min()), int(xs.min())
    height, width = int(ys.max()) - top + 1, int(xs.max()) - left + 1
    cx = float(xs[ys <= top + max(1.0, EDGE_BAND * height)].mean())
    cy = float(ys[xs <= left + max(1.0, EDGE_BAND * width)].mean())
    rx, ry = cx - left, cy - top
    if rx <= 0 or ry <= 0:
        return None, "degenerate shape"
    aspect, size = rx / ry, ry / text_height
    upper = mask[max(0, int(cy + SYMMETRY_ROWS[0] * ry)) : int(cy + SYMMETRY_ROWS[1] * ry) + 1]
    columns = np.nonzero(upper.any(axis=0))[0]
    symmetry = float((columns.max() - cx) / rx) if columns.size else 0.0
    trace.measure("aspect", aspect)
    trace.measure("size", size)
    trace.measure("symmetry", symmetry)
    shaped = ASPECT_TEAM[0] <= aspect <= ASPECT_TEAM[1] or ASPECT_SELF[0] <= aspect <= ASPECT_SELF[1]
    sized = SIZE_RANGE[0] <= size <= SIZE_RANGE[1]
    if not (shaped and sized and abs(symmetry - 1.0) <= SYMMETRY_TOLERANCE):
        return None, f"Rx/Ry {aspect:.2f}, Ry {size:.2f} text lines, right/left extent {symmetry:.2f}"
    return _Icon(mask, cx, cy, rx, ry, v_lit, step), ""


def _decide_mode(icon: _Icon, hsv: _Hsv, trace: _Trace) -> tuple[ImprintMode | None, float]:
    """Aspect and centre disk must agree; the confidence grows with both distances to their splits."""
    aspect = icon.rx / icon.ry
    ys, xs = np.ogrid[: icon.mask.shape[0], : icon.mask.shape[1]]
    disk = (xs - icon.cx) ** 2 + (ys - icon.cy) ** 2 <= (CENTRE_RADIUS * icon.ry) ** 2
    lit_icon = icon.mask & (hsv.v >= LIT_V * icon.v_lit)
    centre = float(lit_icon[disk].mean()) if disk.any() else 0.0
    trace.measure("centre", centre)
    trace.measure("v_lit", icon.v_lit)
    by_aspect = ImprintMode.TEAM if aspect >= ASPECT_SPLIT else ImprintMode.SELF
    by_centre = ImprintMode.SELF if centre >= CENTRE_SPLIT else ImprintMode.TEAM
    if by_aspect is not by_centre:
        trace.warn(
            f"mode features disagree: Rx/Ry {aspect:.2f} says {by_aspect.value}, centre {centre:.2f} says "
            f"{by_centre.value}"
        )
        return None, 0.0
    if abs(aspect - ASPECT_SPLIT) < ASPECT_MARGIN:
        trace.warn(f"Rx/Ry {aspect:.2f} is within {ASPECT_MARGIN} of the self/team split {ASPECT_SPLIT}")
    confidence = min(1.0, abs(aspect - ASPECT_SPLIT) / (2 * ASPECT_MARGIN)) * min(
        1.0, abs(centre - CENTRE_SPLIT) / CENTRE_MARGIN
    )
    if icon.dim_step > 0:
        confidence *= RETRY_CONFIDENCE
    return by_aspect, confidence


def _structure_ok(mode: ImprintMode, icon: _Icon, trace: _Trace) -> bool:
    """Team: square bodies present, free corners and centre empty. Self: crosshair ring on the free diagonals, centre
    filled, box corners mostly empty. Added after windows over the imprint TEXT passed the geometry checks."""
    cover = {name: icon.coverage(*zone) for name, zone in STRUCTURE_ZONES.items()}
    for name, value in cover.items():
        trace.measure(f"zone_{name}", value)
    corners = max(cover["c_tl"], cover["c_tr"], cover["c_bl"])
    if mode is ImprintMode.TEAM:
        squares = min(cover["sq_top"], cover["sq_left"], cover["sq_right"])
        ok = squares >= TEAM_SQUARE_MIN and corners <= TEAM_CORNER_MAX and cover["ctr"] <= TEAM_CENTRE_MAX
    else:
        ring = min(cover["d_tl"], cover["d_tr"], cover["d_bl"])
        ok = ring >= SELF_RING_MIN and cover["ctr"] >= SELF_RING_MIN and corners <= SELF_CORNER_MAX
    if not ok:
        shown = ", ".join(f"{k} {v:.2f}" for k, v in cover.items())
        trace.warn(f"{mode.value} icon structure not found (zone coverage {shown}): not an imprint icon?")
    return ok


def _lit_squares(icon: _Icon, hsv: _Hsv, trace: _Trace) -> Mapping[str, str]:
    """Each square's visible part: median brightness relative to V_lit (unlit squares are the same hue, ~30 %)."""
    half_u, half_v = SQUARE_HALF
    states: dict[str, str] = {}
    for square in SQUARES:
        u, v = SQUARE_CENTRES[square]
        u_range, v_range = (u - half_u, u + half_u), (v - half_v, v + half_v)
        if square == "right":
            v_range = (v - half_v, v)
        elif square == "bottom":
            u_range = (u - half_u, u - BOTTOM_SQUARE_CUT)
        zone = icon.zone(u_range, v_range)
        if zone is None:
            states[square] = OCCLUDED
            continue
        mask = icon.mask[zone]
        cover = float(mask.mean())
        trace.measure(f"square_{square}_cover", cover)
        if cover < SQUARE_MIN_COVER:
            states[square] = OCCLUDED
            continue
        level = float(np.median(hsv.v[zone][mask])) / icon.v_lit
        trace.measure(f"square_{square}_level", level)
        states[square] = LIT if level >= LIT_MIN else DARK if level <= DARK_MAX else UNKNOWN
    undecided = [square for square, state in states.items() if state not in (LIT, DARK)]
    if undecided:
        trace.warn(f"lit state not read for the {', '.join(undecided)} square(s)")
    return MappingProxyType(states)


@dataclass(frozen=True, slots=True)
class _Letters:
    count: int | None
    confidence: float
    colour: str


def _count_letters(icon: _Icon, hsv: _Hsv, trace: _Trace) -> _Letters:
    """Grade letters in the band below the icon footprint: valleys of the column profile, checked against the block
    width and the blob count; their colour measured on its own (without the icon-hue filter)."""
    zone = icon.zone(LETTER_COLUMNS, LETTER_BAND)
    if zone is None:
        trace.warn("the grade-letter band is outside the window")
        return _Letters(None, 0.0, UNKNOWN)
    mask, value = icon.mask[zone], hsv.v[zone]
    fill = mask & (value >= LETTER_FILL * float(np.percentile(value[mask], 90))) if mask.any() else mask
    if int(fill.any(axis=1).sum()) < 2:
        trace.warn("no grade letters below the icon")
        return _Letters(0, NO_LETTERS_CONFIDENCE, UNKNOWN)
    columns = fill.sum(axis=0).astype(np.float64)
    cumulative = np.cumsum(columns) / columns.sum()
    first, last = (int(np.searchsorted(cumulative, q)) for q in LETTER_SPAN)
    width = (last - first + 1) / icon.ry
    sigma = max(MIN_BLUR_PX, PROFILE_BLUR * icon.ry)
    blurred = cv2.GaussianBlur(columns[None, :], (0, 0), sigmaX=sigma)
    profile = np.asarray(blurred, dtype=np.float64).ravel()[first : last + 1]
    segments = _count_segments(profile)
    blobs = _count_blobs(fill, BLOB_MIN_AREA * icon.ry * icon.ry)
    colour = _letter_colour(hsv, zone, BLOB_MIN_AREA * icon.ry * icon.ry)
    trace.measure("letters_width", width)
    trace.measure("letters_segments", segments)
    trace.measure("letters_blobs", blobs)
    low, high = LETTER_WIDTH_PER[0] * segments, LETTER_WIDTH_PER[1] * segments + LETTER_WIDTH_PER[2]
    if not (1 <= segments <= MAX_LETTERS and low <= width <= high):
        trace.warn(
            f"letter count unresolved: {segments} segment(s) but block width {width:.2f} Ry "
            f"(compatible range {low:.2f}-{high:.2f})"
        )
        return _Letters(None, 0.0, colour)
    if blobs != segments:
        trace.warn(f"letter count unresolved: {segments} segment(s) but {blobs} blob(s)")
        return _Letters(None, 0.0, colour)
    by_width = next((n for lo, hi, n in WIDTH_BINS if lo <= width < hi), None)
    confidence = LETTERS_AGREE_CONFIDENCE if by_width == segments else LETTERS_WIDTH_ONLY_CONFIDENCE
    return _Letters(segments, confidence, colour)


def _count_segments(profile: npt.NDArray[np.float64]) -> int:
    """Peaks of the column profile with hysteresis (enter at PEAK_ENTER x peak, leave below PEAK_LEAVE x peak)."""
    peak = float(profile.max()) if profile.size else 0.0
    segments, inside = 0, False
    for value in profile:
        if not inside and value >= PEAK_ENTER * peak:
            segments, inside = segments + 1, True
        elif inside and value < PEAK_LEAVE * peak:
            inside = False
    return segments


def _count_blobs(fill: Mask, min_area: float) -> int:
    count, _, stats, _ = cv2.connectedComponentsWithStats(fill.astype(np.uint8), connectivity=8)
    return int(sum(1 for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= min_area))


def _letter_colour(hsv: _Hsv, zone: tuple[slice, slice], min_pixels: float) -> str:
    value = hsv.v[zone]
    coloured = (hsv.s[zone] >= BRIGHT_S) & (value >= LETTER_COLOUR_V * float(np.percentile(value, 99)))
    if coloured.sum() < min_pixels:
        return UNKNOWN
    return colour_family(_dominant_hue(hsv.h[zone][coloured]))


def _grade(colour: str, letters: _Letters, trace: _Trace) -> tuple[ImprintGrade | None, float]:
    """Only the observed (colour, letter count) pairs of GRADE_RULES name a grade; anything else is None + warning."""
    if letters.count is None or letters.count == 0:
        return None, 0.0
    if letters.colour != colour:
        trace.warn(f"grade letters look {letters.colour} but the icon is {colour}: grade not read")
        return None, 0.0
    rule = GRADE_RULES.get((colour, letters.count))
    if rule is None:
        seen = ", ".join(f"{c} x{n} = {r.grade.value}" for (c, n), r in GRADE_RULES.items())
        trace.warn(
            f"grade not read: {colour} icon with {letters.count} letter(s) "
            f"(width {trace.measurements.get('letters_width')} Ry) was never seen (seen: {seen})"
        )
        return None, 0.0
    return rule.grade, letters.confidence


def _read_grey(hsv: _Hsv, text_height: float, trace: _Trace) -> ImprintIconReading:
    """No coloured icon: look for the Locked structure (a white padlock and grey squares with holes up-left of it)."""
    white = _white_pixels(hsv)
    padlocks = _padlocks(white, text_height)
    squares = _grey_squares(hsv, white, text_height)
    up_left = max((sum(1 for gx, gy in squares if gx < lx and gy < ly) for lx, ly in padlocks), default=0)
    trace.measure("padlocks", len(padlocks))
    trace.measure("grey_squares", len(squares))
    trace.measure("grey_squares_up_left", up_left)
    if up_left >= MIN_GREY_SQUARES:
        return trace.reading(colour="grey", locked=True)
    return trace.reading(colour="grey", warning="no coloured imprint icon and no Locked icon found")


def _white_pixels(hsv: _Hsv) -> Mask:
    """Near-white pixels clearly brighter than the window (none when nothing stands out)."""
    background = float(np.median(hsv.v))
    top = float(np.percentile(hsv.v, 99.5))
    if top < LOCK_CONTRAST * max(background, 1.0):
        return np.zeros(hsv.v.shape, dtype=bool)
    return (hsv.s <= LOCK_WHITE_S) & (hsv.v >= LOCK_WHITE_V * top)


def _padlocks(white: Mask, text_height: float) -> list[tuple[float, float]]:
    """Centres of near-white blobs shaped like the padlock (taller than wide, about one text line, half filled)."""
    count, _, stats, _ = cv2.connectedComponentsWithStats(white.astype(np.uint8), connectivity=8)
    found = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if (
            LOCK_ASPECT[0] <= h / w <= LOCK_ASPECT[1]
            and LOCK_HEIGHT[0] <= h / text_height <= LOCK_HEIGHT[1]
            and LOCK_FILL[0] <= area / (w * h) <= LOCK_FILL[1]
        ):
            found.append((x + w / 2, y + h / 2))
    return found


def _grey_squares(hsv: _Hsv, white: Mask, text_height: float) -> list[tuple[float, float]]:
    """Centres of desaturated shapes brighter than the background, wider than tall, with a small hole."""
    background_s, background_v = float(np.median(hsv.s)), float(np.median(hsv.v))
    grey = (hsv.s <= GREY_S * background_s) & (hsv.v >= GREY_V * background_v) & ~white
    contours, hierarchy = cv2.findContours(grey.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    found: list[tuple[float, float]] = []
    if not contours:  # the hierarchy is None then
        return found
    for i, contour in enumerate(contours):
        child, parent = int(hierarchy[0][i][2]), int(hierarchy[0][i][3])
        if parent != -1 or child == -1:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        hole = cv2.contourArea(contours[child]) / (w * h)
        if (
            GREY_ASPECT[0] <= w / h <= GREY_ASPECT[1]
            and GREY_HEIGHT[0] <= h / text_height <= GREY_HEIGHT[1]
            and GREY_HOLE[0] <= hole <= GREY_HOLE[1]
        ):
            found.append((x + w / 2, y + h / 2))
    return found


def _text_colour(image: BgrImage, boxes: tuple[Box, ...]) -> tuple[str, int | None]:
    """Colour of the imprint text itself (an independent view of the grade colour): family and hue, or grey."""
    hues: list[Channel] = []
    pixels = 0
    for box in boxes:
        y0, y1 = max(0, int(box.y0)), min(image.shape[0], int(box.y1))
        x0, x1 = max(0, int(box.x0)), min(image.shape[1], int(box.x1))
        if x1 <= x0 or y1 <= y0:
            continue
        hsv = _Hsv.of(np.ascontiguousarray(image[y0:y1, x0:x1]))
        bright = (hsv.s >= BRIGHT_S) & (hsv.v >= BRIGHT_V)
        hues.append(hsv.h[bright])
        pixels += bright.size
    coloured = np.concatenate(hues) if hues else np.zeros(0, dtype=np.int32)
    if not pixels or coloured.size < MIN_TEXT_COLOUR_FRACTION * pixels:
        return "grey", None
    hue = _dominant_hue(coloured)
    return colour_family(hue), hue


def _check_text_colour(reading: ImprintIconReading, text: tuple[str, int | None]) -> ImprintIconReading:
    """Cross-check (warning only): the imprint text takes the grade colour like the icon (MECH-IMP-02)."""
    family, hue = text
    measurements = dict(reading.measurements)
    if hue is not None:
        measurements["text_hue"] = float(hue)
    warnings, grade_confidence = reading.warnings, reading.grade_confidence
    if reading.colour != UNKNOWN and family != reading.colour:
        warnings = (*warnings, f"the imprint text looks {family} but the icon {reading.colour}")
        grade_confidence = round(grade_confidence * TEXT_MISMATCH_FACTOR, 3)
    return replace(
        reading, measurements=MappingProxyType(measurements), warnings=warnings, grade_confidence=grade_confidence
    )
