"""Read the gear panel of the Hero Info screen (M7): the six gear pieces, the artifact and the Exclusive Equipment.

What the panel shows (user captures 2026-10-04, Stove PC client, English):
- "Average Equipment Score: N" (`labels.EQUIPMENT_SCORE`) above two columns of three pieces: left weapon / helmet /
  armor, right necklace / ring / boots (`GearSlot`). An empty slot shows a silhouette and no text; a hero without
  gear shows no score line at all;
- per piece: the item icon, with the item level at its top-left, a red "+N" pill at its top-right (no pill at +0),
  the piece score under it and the set badge at its bottom-right (read by `sets`); right of the icon, right-aligned,
  the main stat value and up to four substat values (MECH-GEAR-01, MECH-GEAR-03), each with a stat icon on its left
  (read by `stat_icons` in `ValueRow.icon_window`);
- above the score line, right: the artifact (a "+N" pill at the top-right of its icon, red or orange, none at +0;
  "Lv.X/Y" right of the icon; the name below, often cut by the artwork); left of it, only for heroes that have one,
  the Exclusive Equipment (MECH-EE-01): a small stat icon and the value ("12%") on one OCR line, the name below.

Evidence: prototype `spikes/m7_gear_layout.py`, 5 Hero Info captures with gear plus 1 without, each at UI scales
0.64 / 1.0 / 1.28 (2000-px copies; the user's window is 2560 px). Slots, main and substat values, '+N', score and
artifact code: 30/30 pieces and 5/5 artifacts correct at every scale, 0 wrong; item level 29/30 at 0.64 (one
abstention), 30/30 above; frame colour 29/29 with the one ambiguous piece sent to REVIEW at every scale. Deleting OCR
lines (empty slots, a missed main or substat) never moved a piece to a wrong slot nor promoted a substat to main.

Method (lengths in the unit h, `GearPanel.unit`; no pixel position is hard-coded):
1. anchor: the "Average Equipment Score" line; without it there is no panel (`present` False, with a warning when
   gear-like text is visible anyway);
2. value columns: value-shaped lines below the anchor, clustered by RIGHT edge (the values are right-aligned); a
   cluster is a stat list only if two of its rows are closer than LIST_ROW_MAX (item levels and scores repeat once
   per piece). h = their row pitch / ROW_PITCH (the anchor box height is padded by the detector at small scales);
3. pieces: a column is cut top to bottom into pieces (PIECE_SPAN); the slot is the piece's grid row, counted from
   the anchor (FIRST_MAIN_ROW + k x the measured piece pitch). The first row is the main stat only if it sits on
   that grid row: a substat is never promoted to main; a missed main or substat is re-read by a second OCR pass on
   the expected cell, only where the image shows text ink (a row is never invented);
4. icon fields: score (its centre gives the icon centre), '+N' and item level from the OCR lines of the icon block;
   missing or odd ones are re-read on upscaled crops in several renderings through the same `TextReader`, and a
   value needs at least MIN_VOTES agreeing renderings and a unique top (single renderings do misread "90" as "70",
   "20" or "50"). No '+N' means +0 only when the red pill is visibly absent;
5. frame colour: the colour of the icon's background (edge strips, away from the art) and the gold corner ornament
   must agree, else REVIEW. Red frame = epic, purple = heroic is community knowledge, kept `assumed`: this module
   reports the colour only (`GearGrade` is the caller's mapping);
6. artifact: "Lv.X/Y" line right of the anchor and above it; the name below it -> catalog code (exact, else the
   margin rule of `labels.match_label`); '+N' from the pill (OCR line, second pass, else +0 only with no pill);
7. Exclusive Equipment: a value line with a name below it, above the anchor and left of the artifact; any other text
   left in that area is a warning (an EE missed by OCR must not look like "no EE").

Consistency checks only lower confidence or add warnings, they never fix a value: average score = floor(sum of the
six scores / 6) (5/5 captures, `assumed`); artifact level = 1 + floor(N / 3) for "+N" (community rule, unverified;
MECH-ART-03 keeps the per-level steps `assumed`).
Limits: one client, one layout, English only (SPEC D39); 1 Exclusive Equipment, 5 purple pieces and 3 artifacts at
+0 seen; the thresholds were measured on these captures only.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import combinations, pairwise
from typing import Final, Literal

import cv2
import numpy as np
import numpy.typing as npt

from e7ac.domain.roster import GearSlot
from e7ac.settings import GameLanguage
from e7ac.vision.labels import EQUIPMENT_SCORE, LEVEL_MAX, LEVEL_PREFIX, match_label, normalise
from e7ac.vision.ocr import Box, TextLine, TextReader

type BgrImage = npt.NDArray[np.uint8]
type Side = Literal["L", "R"]
type RenderMode = Literal["colour", "value", "low_saturation", "clahe"]
type Window = tuple[float, float, float, float]
"""(x0, y0, x1, y1) relative to a reference point, in units h."""

# --- the unit h (all lengths below are in h unless stated otherwise) ---
ROW_PITCH: Final = 0.72
"""Substat row pitch (centre to centre) in h; h = measured row pitch / ROW_PITCH. The anchor box height is only a
first guess: the detector pads boxes at small UI scales (anchor height / h measured 0.95-1.22), the pitch is
padding-free (h / UI scale measured 43.4-44.5 px on the 2000-px copies at all 15 runs)."""
PITCH_SAMPLE_RANGE: Final = (0.4, 1.0)
"""Two rows of one value column whose centres are this many anchor heights apart measure the row pitch."""
MIN_PITCH_SAMPLES: Final = 3

# --- anchor (in anchor line heights) ---
SPLIT_SCORE_ROW: Final = 0.4
"""The average score read as its own line: centre within this of the label's centre ..."""
SPLIT_SCORE_GAP: Final = 2.0
"""... and starting at most this far right of the label."""
SCORE_DIGITS_MAX: Final = 3
"""Scores seen: 24-100 (pieces), 48-91 (averages); a longer digit run glued to the label is not taken as one."""

# --- value columns (in anchor heights while the unit is unknown, then in h) ---
COLUMN_TOLERANCE: Final = 0.35
"""Right edges of one value column agree within this (right-aligned text)."""
LIST_ROW_MAX: Final = 1.2
"""A stat list has two rows closer than this; item levels and scores repeat once per piece (4.5 h apart)."""
COLUMN_LEFT_MARGIN: Final = 1.5
"""Value lines start right of the anchor's left edge - this."""
RIGHT_COLUMN_MIN: Final = 9.4
"""One column only: it is the right one when its right edge is more than this right of the anchor's left edge
(measured: left column 5.2 h, right column 13.2 h). A flagged fallback."""
GLUED_GLYPHS_MAX: Final = 2
"""A value line may start with up to this many non-digit characters: the stat icon read as a glyph ..."""
GLUED_CONFIDENCE_MAX: Final = 0.6
"""... but such a glyph may also be a misread digit ('l2%'): the value is kept with at most this confidence."""

# --- pieces and slots ---
PIECE_SPAN: Final = 3.4
"""Rows less than this below a piece's first row belong to it: main -> 4th substat 3.0 h, 1st substat -> next main
3.7-3.8 h, so a missed row never splits or merges pieces."""
FIRST_MAIN_ROW: Final = 1.53
"""Anchor centre -> first main-stat row centre: measured 1.47-1.60 (5 captures x 3 scales)."""
PIECE_PITCH_FALLBACK: Final = 4.58
"""Main row -> next main row when it cannot be measured (measured 4.45-4.57)."""
SLOT_FRACTION_MAX: Final = 0.3
"""A piece more than this many piece pitches off its grid row gets no slot (REVIEW)."""
MAIN_ALIGNMENT: Final = 0.35
"""The first row is the main stat only within this of its grid row (main -> 1st substat measured 0.74-0.83)."""
ROWS_MAX: Final = 5
"""1 main + 4 substats (MECH-GEAR-03)."""
ROW_GAP_MAX: Final = 1.6
"""In row pitches: a bigger gap between two rows of a piece means a row was missed."""

# --- item icon block, relative to (icon centre x, main row centre y) ---
ICON_BAND: Final = (7.0, 3.0)
"""The icon texts lie between the column's right edge - 7 h and - 3 h ..."""
ICON_BAND_Y: Final = (-1.0, 3.6)
"""... and between the main row - 1 h and + 3.6 h."""
TOP_TEXT_MAX: Final = 1.2
"""Item level and '+N' are centred above main row + this; ..."""
SCORE_TEXT_MIN: Final = 1.6
"""... the score below main row + this."""
ICON_CENTRE_FALLBACK: Final = 4.85
"""Column right edge - icon centre when no score of the column was read."""
ICON_BOX: Final[Window] = (-1.22, -0.12, 1.22, 2.25)
"""The item artwork square (measured 105 px on the 2000-px copies): used for the frame colour and by `sets`."""
LEVEL_DIGITS: Final = 2
"""First-pass item levels other than 2 digits ('901', '881': the art read as a trailing 1) are re-read."""

# --- value cells and their stat-icon windows (in substat row pitches p) ---
ICON_WINDOW_LEFT: Final = 4.6
ICON_WINDOW_RIGHT: Final = 1.5
ICON_WINDOW_HALF_HEIGHT: Final = 0.75
"""Stat-icon window [right edge - 4.6 p, min(value left, right edge - 1.5 p)] x (row centre +- 0.75 p): the
`stat_icons.IconWindow` contract (measured icon centre: right edge - 3.1 p)."""
MAIN_CELL: Final = (2.5, 0.45)
SUB_CELL: Final = (1.5, 0.35)
"""Estimated (width, half height) of a value cell in h, for a row known only by its position."""

# --- second OCR passes ---
RENDER_TARGET: Final = 100.0
"""Crops are upscaled to about this many pixels per h ..."""
MIN_UPSCALE: Final = 1.5
"""... and at least by this factor."""
RENDER_BORDER: Final = 0.4
"""Plain border around a rendered crop, in h at the rendered scale (the detector misses text touching the edge)."""
LOW_SATURATION_GAIN: Final = 1.6
CLAHE_CLIP: Final = 2.0
CLAHE_TILES: Final = 4
MIN_VOTES: Final = 2
SECOND_PASS_BELOW: Final = 0.5
"""A first-pass field below this confidence is re-read."""
VOTE_CONFIDENCE: Final = (0.5, 0.1)
"""Confidence of a voted value: 0.5 + 0.1 per agreeing rendering (5 of 5 -> 1.0)."""
ODD_FIRST_PASS: Final = 0.3
"""Confidence of a first-pass read of the wrong shape (kept only until the second pass)."""
_TRAILING_PUNCTUATION: Final = re.compile(r"[.,:;'`]+$")
"""'85.', '90,': artefacts of the ornament or the art, dropped before voting."""


@dataclass(frozen=True, slots=True)
class _Rendering:
    window: Window
    mode: RenderMode
    target: float = RENDER_TARGET


# Item level: the number sits on a gold corner ornament (read as 'F', '¥', '新' before it) and on the item art (read
# as a trailing '1'); tight crops drop both. These 5 renderings were chosen by a sweep (3 windows x 6 renderings x
# 2 upscales on 90 pieces x 3 scales): each had 0 wrong 2-digit reads; do not reduce their number (the vote is what
# protects against single misreads).
_LEVEL_TIGHT: Final[Window] = (-1.08, -0.35, -0.33, 0.65)
_LEVEL_TIGHT_LOW: Final[Window] = (-1.05, -0.3, -0.38, 0.55)
_LEVEL_MID: Final[Window] = (-1.15, -0.45, -0.25, 0.75)
LEVEL_RENDERINGS: Final = (
    _Rendering(_LEVEL_TIGHT, "colour"),
    _Rendering(_LEVEL_TIGHT, "value"),
    _Rendering(_LEVEL_TIGHT_LOW, "low_saturation"),
    _Rendering(_LEVEL_MID, "value"),
    _Rendering(_LEVEL_MID, "clahe"),
)
_BADGE_WIDE: Final[Window] = (0.0, -0.9, 1.8, 0.5)
_BADGE_TIGHT: Final[Window] = (0.25, -0.65, 1.65, 0.35)
ENHANCE_RENDERINGS: Final = (
    _Rendering(_BADGE_WIDE, "colour"),
    _Rendering(_BADGE_TIGHT, "colour"),
    _Rendering(_BADGE_TIGHT, "value"),
)
_SCORE_WINDOW: Final[Window] = (-1.3, 2.1, 1.3, 3.5)
SCORE_RENDERINGS: Final = (_Rendering(_SCORE_WINDOW, "colour"), _Rendering(_SCORE_WINDOW, "value"))

# --- missed rows (relative to (column right edge, row centre)) ---
INK_WINDOW: Final[Window] = (-1.5, -0.3, 0.05, 0.3)
INK_CONTRAST: Final = 45
"""Ink = pixels brighter than the window's median by this (V levels) ..."""
INK_SATURATION_MAX: Final = 90
"""... and not saturated (substat values are semi-transparent grey over a varying background)."""
ROW_INK_MIN: Final = 0.2
"""A missing row is re-read only if its ink is at least this x the weakest read row of the piece (rows read:
>= 0.31; empty 4th substat of a heroic +0 piece: 0.0; empty cells at most 0.17)."""
MAIN_INK_SHARE: Final = 0.6
"""Without a read substat, the reference ink is this x the main row's (main values are bigger and opaque)."""
PROBE_LEFT: Final = (3.0, 2.0)
"""Re-read cell: from the right edge - this (main, substat) ..."""
PROBE_RIGHT: Final = 0.2
PROBE_HALF_HEIGHT: Final = 0.6
"""... to + PROBE_RIGHT, row centre +- PROBE_HALF_HEIGHT."""
PROBE_ROW_TOLERANCE: Final = 0.35
"""A re-read line counts only if its centre maps back within this many row pitches of the expected row ..."""
PROBE_EDGE_TOLERANCE: Final = 0.5
"""... and its right edge within this of the column's right edge (a neighbouring row is never taken)."""
PROBE_RENDERINGS: Final[tuple[RenderMode, ...]] = ("colour", "value")

# --- '+N' pill and frame colour, relative to (icon centre x, main row centre y); OpenCV HSV (hue 0-179) ---
PILL_WINDOW: Final[Window] = (0.4, -0.4, 1.4, 0.2)
"""Inside the measured pill [icon cx + 0.37 h, cx + 1.44 h] x [row - 0.45 h, row + 0.23 h]."""
PILL_ABSENT_MAX: Final = 0.04
"""Bright-red share below which there is no pill: +0 pieces 0.000 (n=12), +N pieces 0.37-0.47 (n=78)."""
RED_HUE_MAX: Final = 8
RED_HUE_WRAP: Final = 172
"""Bright red: hue <= RED_HUE_MAX or >= RED_HUE_WRAP ..."""
BRIGHT_SATURATION_MIN: Final = 150
BRIGHT_VALUE_MIN: Final = 170
"""... with S and V above these."""
FRAME_STRIP: Final = 0.3
"""Width of the icon-edge strips where the background colour is voted (the art rarely reaches them): the left edge
below the item level, the top edge between level and pill, the bottom-left edge."""
FRAME_LEFT_STRIP_TOP: Final = 0.75
FRAME_TOP_STRIP_X: Final = (1.15, 1.5)
FRAME_BOTTOM_STRIP_WIDTH: Final = 1.2
MIN_STRIP_PX: Final = 2
FRAME_SATURATION_MIN: Final = 90
FRAME_VALUE_RANGE: Final = (40, 210)
FRAME_RED_HUE_MAX: Final = 10
FRAME_PURPLE_HUE: Final = (135, 172)
"""Red frame: hue <= 10 or >= 172 (pieces: mean hue 0.6-1.8); purple: 135-171 (pieces: mean hue 157.5-159.0)."""
PURPLE_MIN: Final = 0.8
RED_MAX: Final = 0.2
"""Purple share of the red + purple votes: purple pieces 0.97-1.00 (n=5), red pieces 0.00-0.08 (n=24); between the
two bounds the colour is mixed (one piece: 0.50-0.54) -> REVIEW."""
FRAME_COVERAGE_MIN: Final = 0.2
"""Red + purple votes as a share of the strips; below it the colour is not measurable -> REVIEW."""
ORNAMENT_WINDOW: Final[Window] = (-1.25, -0.45, -1.0, 0.15)
"""Left of the item level: the gold corner ornament and inner frame seen on every red piece and on no purple one."""
GOLD_HUE: Final = (12, 32)
GOLD_SATURATION_MIN: Final = 70
GOLD_VALUE_MIN: Final = 120
ORNAMENT_MIN: Final = 0.08
ORNAMENT_ABSENT_MAX: Final = 0.04
"""Gold share in ORNAMENT_WINDOW: red pieces 0.16-0.30, purple pieces 0.00-0.01."""
INFERRED_ZERO: Final = 0.9
"""Confidence of +0 inferred from no '+N' read and no pill."""

# --- artifact, relative to the "Lv." line (left edge x0, centre cy) ---
ARTIFACT_ABOVE: Final = 6.0
"""The "Lv." line is above the anchor, within this, and right of the anchor's left edge."""
ARTIFACT_NAME_X: Final = 0.6
ARTIFACT_NAME_DY: Final = (0.3, 1.6)
"""The name starts within ARTIFACT_NAME_X of the "Lv." line's left edge, centred 0.3-1.6 h below it."""
ARTIFACT_BADGE_X: Final = (-2.5, 0.3)
ARTIFACT_BADGE_Y: Final = (-1.6, 0.2)
"""A first-pass '+N' line ends within ARTIFACT_BADGE_X of lv.x0 and is centred within ARTIFACT_BADGE_Y of lv.cy
(measured pill: [lv.x0 - 0.78 h, lv.x0] x [lv.cy - 0.94 h, lv.cy - 0.41 h])."""
_ARTIFACT_PILL_TIGHT: Final[Window] = (-0.9, -1.0, 0.05, -0.33)
ARTIFACT_RENDERINGS: Final = (
    _Rendering(_ARTIFACT_PILL_TIGHT, "colour", 120.0),
    _Rendering(_ARTIFACT_PILL_TIGHT, "colour", 180.0),
    _Rendering(_ARTIFACT_PILL_TIGHT, "colour", 240.0),
    _Rendering((-1.2, -1.15, 0.2, -0.2), "colour", 120.0),
)
"""Colour only: binary renderings read '+30' as '+3'. The art left of the pill is read glued to it ('7+4', 'Z|+4'),
so the '+N' token is taken from the END of each line."""
ARTIFACT_PILL_WINDOW: Final[Window] = (-0.75, -0.9, -0.05, -0.45)
ARTIFACT_PILL_HUE_MAX: Final = 22
"""The artifact pill is red ('+15', '+30') or orange ('+4'): hue <= 22 or >= RED_HUE_WRAP, bright."""
ARTIFACT_PILL_ABSENT_MAX: Final = 0.15
"""Red/orange share below which there is no pill: +0 0.051-0.057 (n=3), +N 0.288-0.615 (n=12)."""
ARTIFACT_INFERRED_ZERO: Final = 0.8
INCONSISTENT_LEVEL_TEXT: Final = 0.2
INCONSISTENT_ENHANCE: Final = 0.4
"""Confidence caps when "Lv.X/Y" and "+N" disagree (one of the two reads is wrong; we cannot tell which)."""
ARTIFACT_LEVEL_STEP: Final = 3
"""Community rule, unverified: the artifact skill level rises by 1 every 3 enhancement levels (MECH-ART-03 keeps
the per-level steps `assumed`); seen consistent on Max/6 +15, Max/11 +30, 2/10 +4, 1/6 +0."""

# --- Exclusive Equipment, relative to the anchor ---
EE_ABOVE: Final = 6.0
EE_LEFT_MARGIN: Final = 1.5
EE_ARTIFACT_GAP: Final = 3.0
"""The EE lines are above the anchor (within EE_ABOVE), right of its left edge - EE_LEFT_MARGIN and more than
EE_ARTIFACT_GAP left of the artifact (measured: the EE value starts 7.7-7.9 h left of the "Lv." line)."""
EE_NAME_X: Final = 0.6
EE_NAME_DY: Final = (0.3, 1.4)

# --- no anchor ---
GEAR_SIDE: Final = 0.5
"""Fraction of the image width: the gear panel is in the right half of the Hero Info screen."""
GEAR_LIKE_MIN: Final = 2
"""This many value-shaped (or artifact-level) lines there without an anchor -> warning, not a silent "no gear"."""

_VALUE: Final = re.compile(r"\d{1,3}(?:,\d{3})*%?")
_VALUE_TAIL: Final = re.compile(r"\d{1,3}(?:,\d{3})*%?$")
_NUMBER_CHARS: Final = re.compile(r"[\d,.%+]")
_INT: Final = re.compile(r"\d{1,3}")
_TRAILING_DIGITS: Final = re.compile(r"(\d+)\s*$")
_BADGE: Final = re.compile(r"\+\s?(\d{1,2})")
_PLUS_TAIL: Final = re.compile(r"\+\d{1,2}$")
_LEVEL_FIRST: Final = re.compile(r"\d{2,4}")
_EE_VALUE: Final = re.compile(r"\+?(\d{1,3}(?:\.\d)?)(%?)")
_LETTERS: Final = re.compile(r"[A-Za-z]{3}")
_ALNUM: Final = re.compile(r"[^\W_]")

_SLOTS: Final[Mapping[Side, tuple[GearSlot, GearSlot, GearSlot]]] = {
    "L": (GearSlot.WEAPON, GearSlot.HELMET, GearSlot.ARMOR),
    "R": (GearSlot.NECKLACE, GearSlot.RING, GearSlot.BOOTS),
}


# ===================================================================================================== public API


@dataclass(frozen=True, slots=True)
class Read[T]:
    """One parsed field: `value` None = not read, the caller asks for review (never a silent guess)."""

    value: T | None
    confidence: float
    """0..1; a ranking aid, not a probability."""
    note: str = ""
    """How the value was obtained, or why it is missing."""


@dataclass(frozen=True, slots=True)
class ValueRow:
    """One stat value of the panel (main stat, substat or the Exclusive Equipment's stat)."""

    text: str
    """The value as shown ('2,765', '9%'); '' when the row is known only by its position."""
    value: float | None
    """Flat stats in game units (2765.0), rates as fractions ('70%' -> 0.70); None = not read (REVIEW)."""
    percent: bool
    box: Box
    """The OCR line box (estimated for a row that could not be read)."""
    icon_window: Box
    """Where the stat icon left of the value is searched (`stat_icons.IconWindow.box`)."""
    group: str
    """'L.main', 'L.sub', 'R.main', 'R.sub' or 'ee': the `stat_icons.IconWindow.group`."""
    confidence: float


@dataclass(frozen=True, slots=True)
class GearPiece:
    slot: GearSlot
    main: ValueRow
    """Never a promoted substat: a main stat that could not be read has `value` None."""
    subs: tuple[ValueRow, ...]
    """Top to bottom; a row seen in the image but not readable has `value` None."""
    item_level: Read[int]
    enhance: Read[int]
    score: Read[int]
    frame: Read[str]
    """'red' or 'purple' when the background colour and the gold ornament agree, else None (REVIEW). Red = epic,
    purple = heroic is community knowledge (`assumed`)."""
    icon_box: Box
    """The item artwork square; the set badge sits at its bottom-right (`sets.SetIconMatcher.piece_set`)."""
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArtifactPanel:
    code: Read[str]
    name_text: str
    """The name as read ('' when missing); often cut by the artwork ('Custom-Made PowerAnci')."""
    enhance: Read[int]
    level_text: Read[str]
    """'Lv.Max/6', 'Lv.2/10' (spaces removed); the cap may be unreadable on the artwork ('Lv.Max/')."""
    box: Box
    """The text block: the "Lv." line and the name."""


@dataclass(frozen=True, slots=True)
class ExclusivePanel:
    value: ValueRow
    """Group 'ee'; its stat icon is inside the OCR line box, left of the digits."""
    name_text: str
    box: Box
    """The value line and the name."""


@dataclass(frozen=True, slots=True)
class GearPanel:
    present: bool
    """False when there is no "Average Equipment Score" line (no gear equipped, or OCR missed it: see warnings)."""
    anchor: Box | None
    average_score: Read[int]
    unit: float
    """h in pixels: the substat row pitch / ROW_PITCH (about the height of a text line); 0.0 when not present."""
    pieces: dict[GearSlot, GearPiece]
    """A missing key = an empty slot (or a piece that could not be placed: see warnings)."""
    artifact: ArtifactPanel | None
    exclusive: ExclusivePanel | None
    warnings: tuple[str, ...]


def parse_gear_panel(
    image: BgrImage,
    lines: Sequence[TextLine],
    reader: TextReader,
    artifact_names: Mapping[str, str],
    language: GameLanguage = GameLanguage.EN,
) -> GearPanel:
    """Read the gear panel of a Hero Info capture.

    `lines` is the OCR of the whole `image` (BGR); `reader` does the second passes on upscaled crops (the same engine).
    `artifact_names` maps catalog display names to artifact codes, with ambiguous names already removed by the
    caller: a name is matched exactly, else by the margin rule (`labels.match_label`)."""
    bgr = _as_bgr(image)
    texts = _Texts.of(language)
    if texts is None:
        message = f"the gear panel texts are not known yet for game language '{language.value}' (SPEC D39)"
        return _absent(bgr, lines, None, [message])
    anchor = _find_anchor(lines, texts.anchor)
    if anchor is None:
        return _absent(bgr, lines, texts, [])
    warnings: list[str] = []
    grid = _measure_grid(lines, anchor.line, warnings)
    drafts = _place_pieces(_split_pieces(lines, anchor.line, grid), grid, warnings)
    context = _Context(bgr, reader, grid, _first_sub_gap(list(drafts.values()), grid))
    _read_icon_texts(lines, list(drafts.values()), grid)
    pieces = {slot: _finish_piece(context, draft) for slot, draft in drafts.items()}
    _check_average(anchor.average, pieces, warnings)
    artifact = _read_artifact(context, lines, anchor.line, artifact_names, texts, warnings)
    exclusive = _read_exclusive(lines, anchor.line, artifact, grid, warnings)
    return GearPanel(
        present=True,
        anchor=anchor.line.box,
        average_score=anchor.average,
        unit=grid.unit,
        pieces=pieces,
        artifact=artifact,
        exclusive=exclusive,
        warnings=tuple(warnings),
    )


# ===================================================================================================== layout


@dataclass(frozen=True, slots=True)
class _Texts:
    anchor: str
    level: re.Pattern[str]
    """The artifact's "Lv.X/Y" line (spaces removed)."""

    @classmethod
    def of(cls, language: GameLanguage) -> _Texts | None:
        anchor, prefix, top = EQUIPMENT_SCORE.get(language), LEVEL_PREFIX.get(language), LEVEL_MAX.get(language)
        if anchor is None or prefix is None or top is None:
            return None
        head = re.escape(prefix.rstrip("."))
        pattern = rf"{head}\.?(?P<current>{re.escape(top)}|\d{{1,2}})/(?P<cap>\d{{1,2}})?"
        return cls(anchor, re.compile(pattern, re.IGNORECASE))


@dataclass(frozen=True, slots=True)
class _Anchor:
    line: TextLine
    average: Read[int]


@dataclass(frozen=True, slots=True)
class _Grid:
    unit: float
    """h in pixels."""
    pitch: float
    """Substat row pitch p in pixels."""
    columns: Mapping[Side, float]
    """Right edge of each value column."""
    first_row: float
    """Centre y of the first main-stat row."""


@dataclass(frozen=True, slots=True)
class _Row:
    text: str
    value: float | None
    percent: bool
    confidence: float
    box: Box
    left_known: bool
    """box.x0 is the left edge of the digits (an OCR box), not an estimate."""


@dataclass(slots=True)
class _Draft:
    """A piece while it is being read."""

    side: Side
    rows: list[_Row]
    slot: GearSlot | None = None
    row_y: float = 0.0
    """Centre y of the piece's main-stat row (the grid row when the main stat was missed)."""
    main_ok: bool = False
    icon_cx: float | None = None
    level: Read[int] | None = None
    enhance: Read[int] | None = None
    score: Read[int] | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Context:
    image: BgrImage
    reader: TextReader
    grid: _Grid
    first_sub_gap: float | None
    """Main row -> first substat row (centres), measured on this panel."""


def _as_bgr(image: BgrImage) -> BgrImage:
    if image.ndim == 2:
        return np.asarray(cv2.cvtColor(image, cv2.COLOR_GRAY2BGR), dtype=np.uint8)
    return image


def _value_text(text: str) -> tuple[str, bool] | None:
    """The value part of an OCR line ('2,765', '9%') and whether glyphs glued in front of it were dropped (the stat
    icon read as a character); None when the line is not value-shaped."""
    compact = text.replace(" ", "")
    match = _VALUE_TAIL.search(compact)
    if match is None:
        return None
    prefix = compact[: match.start()]
    if not prefix:
        return compact, False
    if len(prefix) > GLUED_GLYPHS_MAX or _NUMBER_CHARS.search(prefix):
        return None
    return match.group(0), True


def _number(text: str) -> tuple[float, bool]:
    """'2,765' -> (2765.0, False); '70%' -> (0.7, True)."""
    if text.endswith("%"):
        return round(float(text[:-1].replace(",", "")) / 100, 6), True
    return float(text.replace(",", "")), False


def _row(line: TextLine) -> _Row | None:
    found = _value_text(line.text)
    if found is None:
        return None
    text, glued = found
    value, percent = _number(text)
    confidence = min(line.score, GLUED_CONFIDENCE_MAX) if glued else line.score
    return _Row(text, value, percent, round(confidence, 3), line.box, left_known=True)


def _find_anchor(lines: Sequence[TextLine], label: str) -> _Anchor | None:
    """The "Average Equipment Score: N" line (exact, else the margin rule on the label part) and N."""
    for line in lines:
        head, number = _split_number(line.text)
        name = head.rstrip(" :;.")
        if not name or match_label(name, {label: label}) is None:
            continue
        similarity = 1.0 if normalise(name) == normalise(label) else _similarity(name, label)
        confidence = round(min(line.score, similarity), 3)
        if number is not None:
            return _Anchor(line, Read(int(number), confidence, "ocr"))
        return _Anchor(line, _split_average(lines, line, confidence))
    return None


def _split_number(text: str) -> tuple[str, str | None]:
    """'Average Equipment Score: 89' -> ('Average Equipment Score: ', '89'); a longer digit run is no score."""
    match = _TRAILING_DIGITS.search(text)
    if match is None or len(match.group(1)) > SCORE_DIGITS_MAX:
        return text, None
    return text[: match.start()], match.group(1)


def _split_average(lines: Sequence[TextLine], anchor: TextLine, confidence: float) -> Read[int]:
    """The average read as its own line on the anchor's row, right after the label."""
    h0 = anchor.box.height
    for line in lines:
        gap = line.box.x0 - anchor.box.x1
        if (
            _INT.fullmatch(line.text.strip())
            and abs(line.box.cy - anchor.box.cy) < SPLIT_SCORE_ROW * h0
            and 0 <= gap < SPLIT_SCORE_GAP * h0
        ):
            return Read(int(line.text.strip()), round(min(confidence, line.score), 3), "ocr (separate line)")
    return Read(None, 0.0, f"average score not readable in {anchor.text!r}")


def _similarity(a: str, b: str) -> float:
    return round(SequenceMatcher(None, normalise(a), normalise(b)).ratio(), 3)


def _measure_grid(lines: Sequence[TextLine], anchor: TextLine, warnings: list[str]) -> _Grid:
    h0 = anchor.box.height
    columns = _value_columns(lines, anchor, h0, warnings)
    pitch = _row_pitch(lines, anchor, columns, h0)
    if pitch is None:
        warnings.append("value row pitch not measurable: unit taken from the anchor height (padding-sensitive)")
        unit, pitch = h0, ROW_PITCH * h0
    else:
        unit = pitch / ROW_PITCH
    return _Grid(unit, pitch, columns, anchor.box.cy + FIRST_MAIN_ROW * unit)


def _below_anchor_values(lines: Sequence[TextLine], anchor: TextLine, h0: float) -> list[TextLine]:
    return [
        ln
        for ln in lines
        if ln.box.cy > anchor.box.y1
        and ln.box.x0 > anchor.box.x0 - COLUMN_LEFT_MARGIN * h0
        and _value_text(ln.text) is not None
    ]


def _value_columns(lines: Sequence[TextLine], anchor: TextLine, h0: float, warnings: list[str]) -> dict[Side, float]:
    """Right edges of the value columns: clusters of right edges with a stat-list spacing."""
    clusters: list[list[TextLine]] = []
    for line in sorted(_below_anchor_values(lines, anchor, h0), key=lambda ln: ln.box.x1):
        if clusters and line.box.x1 - clusters[-1][-1].box.x1 <= COLUMN_TOLERANCE * h0:
            clusters[-1].append(line)
        else:
            clusters.append([line])
    lists: list[tuple[int, float]] = []
    for cluster in clusters:
        ys = sorted(ln.box.cy for ln in cluster)
        if any(b - a < LIST_ROW_MAX * h0 for a, b in pairwise(ys)):
            lists.append((len(cluster), statistics.median(ln.box.x1 for ln in cluster)))
    if len(lists) > 2:
        warnings.append(f"{len(lists)} value-column candidates: kept the 2 with the most rows")
        lists = sorted(lists, reverse=True)[:2]
    edges = sorted(x for _, x in lists)
    if len(edges) == 2:
        return {"L": edges[0], "R": edges[1]}
    if len(edges) == 1:
        side: Side = "R" if edges[0] - anchor.box.x0 > RIGHT_COLUMN_MIN * h0 else "L"
        warnings.append(f"one value column only: taken as the {'right' if side == 'R' else 'left'} one by its offset")
        return {side: edges[0]}
    warnings.append("no gear value column found under the score line")
    return {}


def _column_lines(lines: Sequence[TextLine], anchor: TextLine, edge: float, tolerance: float) -> list[TextLine]:
    """Value lines of one column, top to bottom."""
    return sorted(
        (
            ln
            for ln in lines
            if ln.box.cy > anchor.box.y1 and abs(ln.box.x1 - edge) <= tolerance and _value_text(ln.text) is not None
        ),
        key=lambda ln: ln.box.cy,
    )


def _row_pitch(lines: Sequence[TextLine], anchor: TextLine, columns: Mapping[Side, float], h0: float) -> float | None:
    low, high = PITCH_SAMPLE_RANGE
    gaps: list[float] = []
    for edge in columns.values():
        ys = [ln.box.cy for ln in _column_lines(lines, anchor, edge, COLUMN_TOLERANCE * h0)]
        gaps += [b - a for a, b in pairwise(ys) if low * h0 < b - a < high * h0]
    return statistics.median(gaps) if len(gaps) >= MIN_PITCH_SAMPLES else None


def _split_pieces(lines: Sequence[TextLine], anchor: TextLine, grid: _Grid) -> list[_Draft]:
    h = grid.unit
    drafts: list[_Draft] = []
    for side, edge in grid.columns.items():
        column: list[_Draft] = []
        for line in _column_lines(lines, anchor, edge, COLUMN_TOLERANCE * h):
            row = _row(line)
            if row is None:  # pragma: no cover - _column_lines keeps value-shaped lines only
                continue
            if column and row.box.cy - column[-1].rows[0].box.cy < PIECE_SPAN * h:
                column[-1].rows.append(row)
            else:
                column.append(_Draft(side, [row]))
        drafts += column
    return drafts


def _piece_pitch(starts: Sequence[float], unit: float) -> float:
    """Median main-to-main distance over all pairs of piece starts (a pair n rows apart counts its distance / n):
    a missed main moves one start by 0.8 h and biases a minority of the pairs, in both directions."""
    fallback = PIECE_PITCH_FALLBACK * unit
    samples = []
    for a, b in combinations(starts, 2):
        rows_apart = round(abs(b - a) / fallback)
        if rows_apart >= 1:
            samples.append(abs(b - a) / rows_apart)
    return statistics.median(samples) if samples else fallback


def _place_pieces(drafts: list[_Draft], grid: _Grid, warnings: list[str]) -> dict[GearSlot, _Draft]:
    """Slot of each piece from its grid row; the first row is the main stat only when it sits on that row."""
    h = grid.unit
    piece_pitch = _piece_pitch([d.rows[0].box.cy for d in drafts], h)
    placed: dict[GearSlot, _Draft] = {}
    clashes: set[GearSlot] = set()
    for draft in drafts:
        start = draft.rows[0].box.cy
        fraction = (start - grid.first_row) / piece_pitch
        index = round(fraction)
        if not 0 <= index < len(_SLOTS[draft.side]) or abs(fraction - index) > SLOT_FRACTION_MAX:
            texts = ", ".join(r.text for r in draft.rows)
            warnings.append(f"values [{texts}] are not on the slot grid (row {fraction:.2f}): not placed -> REVIEW")
            continue
        slot = _SLOTS[draft.side][index]
        draft.slot = slot
        draft.row_y = grid.first_row + index * piece_pitch
        draft.main_ok = abs(start - draft.row_y) < MAIN_ALIGNMENT * h
        if draft.main_ok:
            draft.row_y = start
        else:
            draft.warnings.append("first value is not on the main-stat row: the main stat was missed by OCR")
        _check_rows(draft, grid)
        if slot in placed:
            clashes.add(slot)
        placed[slot] = draft
    for slot in clashes:
        warnings.append(f"two value groups fall on the {slot.value} slot: not placed -> REVIEW")
        del placed[slot]
    return placed


def _check_rows(draft: _Draft, grid: _Grid) -> None:
    if len(draft.rows) > ROWS_MAX:
        draft.warnings.append(f"{len(draft.rows)} value rows, at most {ROWS_MAX} expected -> REVIEW")
    if any(b.box.cy - a.box.cy > ROW_GAP_MAX * grid.pitch for a, b in pairwise(draft.rows)):
        draft.warnings.append("gap between two value rows: a row was probably missed")


def _first_sub_gap(drafts: Sequence[_Draft], grid: _Grid) -> float | None:
    gaps = [
        d.rows[1].box.cy - d.rows[0].box.cy
        for d in drafts
        if d.main_ok and len(d.rows) > 1 and d.rows[1].box.cy - d.rows[0].box.cy < ROW_GAP_MAX * grid.pitch
    ]
    return statistics.median(gaps) if gaps else None


# ===================================================================================================== icon texts


def _read_icon_texts(lines: Sequence[TextLine], drafts: Sequence[_Draft], grid: _Grid) -> None:
    """First pass over the OCR lines of each icon block; the icon centre of a piece without a score is the median
    of its column."""
    for draft in drafts:
        _icon_block_texts(lines, draft, grid)
    for side, edge in grid.columns.items():
        column = [d for d in drafts if d.side == side]
        centres = [d.icon_cx for d in column if d.icon_cx is not None]
        fallback = statistics.median(centres) if centres else edge - ICON_CENTRE_FALLBACK * grid.unit
        for draft in column:
            if draft.icon_cx is None:
                draft.icon_cx = fallback
                draft.warnings.append(
                    "icon centre from the column" if centres else "icon centre from the layout ratio (fallback)"
                )


def _icon_block_texts(lines: Sequence[TextLine], draft: _Draft, grid: _Grid) -> None:
    h, edge = grid.unit, grid.columns[draft.side]
    left, right = edge - ICON_BAND[0] * h, edge - ICON_BAND[1] * h
    top, bottom = draft.row_y + ICON_BAND_Y[0] * h, draft.row_y + ICON_BAND_Y[1] * h
    block = [ln for ln in lines if left <= ln.box.x0 and ln.box.x1 <= right and top <= ln.box.cy <= bottom]
    upper = [ln for ln in block if ln.box.cy < draft.row_y + TOP_TEXT_MAX * h]
    lower = [ln for ln in block if ln.box.cy > draft.row_y + SCORE_TEXT_MIN * h]
    scores = [ln for ln in lower if _INT.fullmatch(ln.text.strip())]
    if scores:
        best = max(scores, key=lambda ln: ln.score)
        draft.score = Read(int(best.text.strip()), round(best.score, 3), "ocr")
        draft.icon_cx = (best.box.x0 + best.box.x1) / 2
    badges = [(ln, m) for ln in upper if (m := _BADGE.fullmatch(ln.text.strip()))]
    if badges:
        line, match = badges[0]
        draft.enhance = Read(int(match.group(1)), round(line.score, 3), "ocr")
    levels = [ln for ln in upper if _LEVEL_FIRST.fullmatch(ln.text.strip())]
    if levels:
        line = min(levels, key=lambda ln: ln.box.x0)
        text = line.text.strip()
        if len(text) == LEVEL_DIGITS:
            draft.level = Read(int(text), round(line.score, 3), "ocr")
        else:
            draft.level = Read(int(text), ODD_FIRST_PASS, f"first pass {text!r}: not {LEVEL_DIGITS} digits")


# ===================================================================================================== pieces


def _finish_piece(context: _Context, draft: _Draft) -> GearPiece:
    """Second passes for the missing icon fields and rows, then the image cues ('+N' pill, frame colour)."""
    grid, h = context.grid, context.grid.unit
    assert draft.slot is not None and draft.icon_cx is not None  # set by _place_pieces / _read_icon_texts
    cx = draft.icon_cx
    level = _second_pass_level(context, draft, cx)
    score = draft.score or _vote_int(_reread(context, cx, draft.row_y, SCORE_RENDERINGS), r"\d{1,3}", "score")
    enhance = draft.enhance or _vote_int(_reread(context, cx, draft.row_y, ENHANCE_RENDERINGS), r"\+\d{1,2}", "'+N'")
    if enhance.value is None:
        enhance = _enhance_without_badge(_pill_share(context.image, cx, draft.row_y, h), enhance)
    main, subs = _probe_rows(context, draft)
    icon_box = _window_box(ICON_BOX, cx, draft.row_y, h)
    frame = _frame_colour(context.image, icon_box, cx, draft.row_y, h)
    for name, read in (("item level", level), ("score", score), ("'+N'", enhance), ("frame colour", frame)):
        if read.value is None:
            draft.warnings.append(f"{name}: {read.note} -> REVIEW")
    return GearPiece(
        slot=draft.slot,
        main=_value_row(main, grid, draft.side, "main"),
        subs=tuple(_value_row(row, grid, draft.side, "sub") for row in subs),
        item_level=level,
        enhance=enhance,
        score=score,
        frame=frame,
        icon_box=icon_box,
        warnings=tuple(draft.warnings),
    )


def _second_pass_level(context: _Context, draft: _Draft, cx: float) -> Read[int]:
    first = draft.level
    if first is not None and first.confidence >= SECOND_PASS_BELOW:
        return first
    voted = _vote_int(_reread(context, cx, draft.row_y, LEVEL_RENDERINGS), r"\d{2}", "item level")
    if voted.value is None and first is not None:
        return Read(None, 0.0, f"{first.note}; {voted.note}")
    return voted


def _enhance_without_badge(pill: float, unread: Read[int]) -> Read[int]:
    """No '+N' read: +0 only when the red pill is visibly absent; a pill we cannot read is a REVIEW."""
    if pill < PILL_ABSENT_MAX:
        return Read(0, INFERRED_ZERO, f"no '+N' and no pill (red share {pill:.3f})")
    return Read(None, 0.0, f"red pill present (share {pill:.2f}) but '+N' unread; {unread.note}")


def _value_row(row: _Row, grid: _Grid, side: Side, kind: str) -> ValueRow:
    edge, p = grid.columns[side], grid.pitch
    right = edge - ICON_WINDOW_RIGHT * p
    if row.left_known:
        right = min(row.box.x0, right)
    cy = row.box.cy
    window = Box(edge - ICON_WINDOW_LEFT * p, cy - ICON_WINDOW_HALF_HEIGHT * p, right, cy + ICON_WINDOW_HALF_HEIGHT * p)
    return ValueRow(row.text, row.value, row.percent, row.box, window, f"{side}.{kind}", row.confidence)


def _estimated_row(edge: float, cy: float, h: float, cell: tuple[float, float]) -> _Row:
    width, half = cell
    return _Row("", None, False, 0.0, Box(edge - width * h, cy - half * h, edge, cy + half * h), left_known=False)


def _probe_rows(context: _Context, draft: _Draft) -> tuple[_Row, list[_Row]]:
    """Main and substat rows, with missed ones re-read where the image shows text ink. Never invents a row: a cell
    without ink is empty (a heroic +0 piece has 3 substats), a cell with ink but no agreeing read is a REVIEW row."""
    grid, h = context.grid, context.grid.unit
    edge = grid.columns[draft.side]
    if draft.main_ok:
        main, subs = draft.rows[0], list(draft.rows[1:])
    else:
        subs = list(draft.rows)
        recovered = _read_cell(context, edge, draft.row_y, PROBE_LEFT[0])
        if recovered is None:
            draft.warnings.append("main stat unreadable -> REVIEW")
            return _estimated_row(edge, draft.row_y, h, MAIN_CELL), subs
        main = recovered
        draft.warnings.append(f"main stat {main.text} recovered by a second OCR pass")
    gap = context.first_sub_gap
    if gap is None:
        if len(subs) < ROWS_MAX - 1:
            draft.warnings.append("fewer than 4 substats and no measured row spacing to look for missed rows")
        return main, subs
    reference = min(
        (_row_ink(context.image, edge, row.box.cy, h) for row in subs),
        default=MAIN_INK_SHARE * _row_ink(context.image, edge, main.box.cy, h),
    )
    found = list(subs)
    for index in range(ROWS_MAX - 1):
        cy = main.box.cy + gap + index * grid.pitch
        if any(abs(row.box.cy - cy) < PROBE_ROW_TOLERANCE * grid.pitch for row in subs):
            continue
        ink = _row_ink(context.image, edge, cy, h)
        if reference <= 0 or ink < ROW_INK_MIN * reference:
            continue
        row = _read_cell(context, edge, cy, PROBE_LEFT[1])
        if row is None:
            draft.warnings.append(
                f"substat {index + 1}: text seen (ink {ink / reference:.2f}) but unreadable -> REVIEW"
            )
            row = _estimated_row(edge, cy, h, SUB_CELL)
        else:
            draft.warnings.append(f"substat {index + 1} ({row.text}) recovered by a second OCR pass")
        found.append(row)
    return main, sorted(found, key=lambda r: r.box.cy)


def _read_cell(context: _Context, edge: float, cy: float, left: float) -> _Row | None:
    """Re-read one value cell in PROBE_RENDERINGS; only lines that map back onto this row and column vote."""
    h, pitch = context.grid.unit, context.grid.pitch
    crop, (ox, oy) = _crop(
        context.image,
        Box(edge - left * h, cy - PROBE_HALF_HEIGHT * h, edge + PROBE_RIGHT * h, cy + PROBE_HALF_HEIGHT * h),
    )
    if crop.size == 0:
        return None
    reads: list[list[str]] = []
    boxes: dict[str, Box] = {}
    for mode in PROBE_RENDERINGS:
        rendered, factor, border = _render(crop, h, mode, RENDER_TARGET)
        texts = []
        for line in context.reader.read(rendered):
            box = Box(
                ox + (line.box.x0 - border) / factor,
                oy + (line.box.y0 - border) / factor,
                ox + (line.box.x1 - border) / factor,
                oy + (line.box.y1 - border) / factor,
            )
            found = _value_text(line.text)
            if found is None:
                continue
            if abs(box.cy - cy) < PROBE_ROW_TOLERANCE * pitch and abs(box.x1 - edge) < PROBE_EDGE_TOLERANCE * h:
                texts.append(found[0])
                boxes.setdefault(found[0], box)
        reads.append(texts)
    vote = _vote(reads, _VALUE.pattern)
    if vote.value is None:
        return None
    value, percent = _number(vote.value)
    return _Row(vote.value, value, percent, vote.confidence, boxes[vote.value], left_known=True)


# ===================================================================================================== second passes


@dataclass(frozen=True, slots=True)
class _Vote:
    value: str | None
    votes: int
    tally: Mapping[str, int]

    @property
    def confidence(self) -> float:
        base, step = VOTE_CONFIDENCE
        return round(min(1.0, base + step * self.votes), 3) if self.value is not None else 0.0


def _vote(reads: Sequence[Sequence[str]], pattern: str) -> _Vote:
    """Majority over renderings: a value needs at least MIN_VOTES renderings and more votes than any other."""
    tally: dict[str, int] = {}
    for texts in reads:
        compact = {re.sub(r"\s+", "", t) for t in texts}  # '+ 15' and '+15' are the same read
        for text in {t for t in compact if re.fullmatch(pattern, t)}:
            tally[text] = tally.get(text, 0) + 1
    if not tally:
        return _Vote(None, 0, tally)
    ranked = sorted(tally.items(), key=lambda kv: -kv[1])
    best, votes = ranked[0]
    unique = len(ranked) == 1 or ranked[1][1] < votes
    return _Vote(best if votes >= MIN_VOTES and unique else None, votes, tally)


def _vote_int(reads: Sequence[Sequence[str]], pattern: str, what: str) -> Read[int]:
    vote = _vote(reads, pattern)
    if vote.value is None:
        return Read(None, 0.0, f"{what} not read (second pass {dict(vote.tally)})")
    number = int(vote.value.lstrip("+"))
    return Read(number, vote.confidence, f"second pass {dict(vote.tally)}")


def _reread(context: _Context, x: float, y: float, renderings: Sequence[_Rendering]) -> list[list[str]]:
    """OCR texts of each rendering of a window around (x, y); trailing punctuation dropped."""
    h = context.grid.unit
    reads: list[list[str]] = []
    for rendering in renderings:
        crop, _origin = _crop(context.image, _window_box(rendering.window, x, y, h))
        if crop.size == 0:
            reads.append([])
            continue
        rendered, _factor, _border = _render(crop, h, rendering.mode, rendering.target)
        texts = [_TRAILING_PUNCTUATION.sub("", ln.text.strip()) for ln in context.reader.read(rendered)]
        reads.append([t for t in texts if t])
    return reads


def _render(crop: BgrImage, unit: float, mode: RenderMode, target: float) -> tuple[BgrImage, float, int]:
    """(rendered image, upscale factor, border in pixels) for one OCR rendering of a crop."""
    factor = max(MIN_UPSCALE, target / unit)
    up = np.asarray(cv2.resize(crop, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC), dtype=np.uint8)
    if mode != "colour":
        hsv = cv2.cvtColor(up, cv2.COLOR_BGR2HSV)
        value = hsv[..., 2]
        if mode == "low_saturation":  # cream digits stay bright, golden / red / purple art darkens
            dimmed = value.astype(np.float32) * (1 - hsv[..., 1].astype(np.float32) / 255) * LOW_SATURATION_GAIN
            value = np.clip(dimmed, 0, 255).astype(np.uint8)
        elif mode == "clahe":  # local contrast on the value channel
            clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP, tileGridSize=(CLAHE_TILES, CLAHE_TILES))
            value = clahe.apply(value)
        up = np.asarray(cv2.cvtColor(value, cv2.COLOR_GRAY2BGR), dtype=np.uint8)
    border = round(RENDER_BORDER * target)
    padded = cv2.copyMakeBorder(up, border, border, border, border, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    return np.asarray(padded, dtype=np.uint8), factor, border


# ===================================================================================================== image probes


def _window_box(window: Window, x: float, y: float, unit: float) -> Box:
    x0, y0, x1, y1 = window
    return Box(x + x0 * unit, y + y0 * unit, x + x1 * unit, y + y1 * unit)


def _crop(image: BgrImage, box: Box) -> tuple[BgrImage, tuple[int, int]]:
    """The part of `box` inside the image and its top-left corner."""
    rows, columns = image.shape[:2]
    x0, y0 = max(0, int(box.x0)), max(0, int(box.y0))
    x1, y1 = min(columns, int(box.x1)), min(rows, int(box.y1))
    if x1 <= x0 or y1 <= y0:
        return image[0:0, 0:0], (x0, y0)
    return image[y0:y1, x0:x1], (x0, y0)


def _hsv(image: BgrImage) -> tuple[npt.NDArray[np.int32], npt.NDArray[np.int32], npt.NDArray[np.int32]]:
    hsv = np.asarray(cv2.cvtColor(image, cv2.COLOR_BGR2HSV), dtype=np.int32)
    return hsv[..., 0], hsv[..., 1], hsv[..., 2]


def bright_red_share(region: BgrImage, hue_max: int = RED_HUE_MAX) -> float:
    """Share of bright, saturated red pixels (hue <= hue_max or >= RED_HUE_WRAP): the '+N' pills."""
    if region.size == 0:
        return 0.0
    hue, saturation, value = _hsv(region)
    red = ((hue <= hue_max) | (hue >= RED_HUE_WRAP)) & (saturation > BRIGHT_SATURATION_MIN) & (value > BRIGHT_VALUE_MIN)
    return float(red.mean())


def _pill_share(image: BgrImage, cx: float, row_y: float, unit: float) -> float:
    crop, _origin = _crop(image, _window_box(PILL_WINDOW, cx, row_y, unit))
    return bright_red_share(crop)


def frame_votes(icon: BgrImage, unit: float) -> tuple[float, float]:
    """(purple share of the red + purple votes, coverage) on the edge strips of an item-icon crop."""
    if icon.size == 0:
        return 0.0, 0.0
    rows, columns = icon.shape[:2]
    strip = max(MIN_STRIP_PX, int(FRAME_STRIP * unit))
    mask = np.zeros((rows, columns), dtype=bool)
    mask[int(FRAME_LEFT_STRIP_TOP * unit) :, :strip] = True
    mask[:strip, int(FRAME_TOP_STRIP_X[0] * unit) : int(FRAME_TOP_STRIP_X[1] * unit)] = True
    mask[-strip:, : int(FRAME_BOTTOM_STRIP_WIDTH * unit)] = True
    hue, saturation, value = _hsv(icon)
    low, high = FRAME_VALUE_RANGE
    voting = mask & (saturation > FRAME_SATURATION_MIN) & (value > low) & (value < high)
    red = int((voting & ((hue <= FRAME_RED_HUE_MAX) | (hue >= FRAME_PURPLE_HUE[1]))).sum())
    purple = int((voting & (hue >= FRAME_PURPLE_HUE[0]) & (hue < FRAME_PURPLE_HUE[1])).sum())
    total = red + purple
    coverage = total / max(1, int(mask.sum()))
    return (purple / total if total else 0.0), coverage


def gold_share(region: BgrImage) -> float:
    """Share of gold pixels (the corner ornament of red-frame pieces)."""
    if region.size == 0:
        return 0.0
    hue, saturation, value = _hsv(region)
    gold = (hue >= GOLD_HUE[0]) & (hue <= GOLD_HUE[1]) & (saturation > GOLD_SATURATION_MIN) & (value > GOLD_VALUE_MIN)
    return float(gold.mean())


def _frame_colour(image: BgrImage, icon_box: Box, cx: float, row_y: float, unit: float) -> Read[str]:
    """Two cues that must agree: the background colour (edge strips) and the gold ornament (red-frame style)."""
    icon, _origin = _crop(image, icon_box)
    purple_share, coverage = frame_votes(icon, unit)
    ornament, _o = _crop(image, _window_box(ORNAMENT_WINDOW, cx, row_y, unit))
    gold = gold_share(ornament)
    return decide_frame(purple_share, coverage, gold)


def decide_frame(purple_share: float, coverage: float, gold: float) -> Read[str]:
    cues = f"purple share {purple_share:.2f}, coverage {coverage:.2f}, ornament {gold:.2f}"
    if coverage < FRAME_COVERAGE_MIN:
        return Read(None, 0.0, f"background colour not measurable ({cues})")
    colour = "purple" if purple_share >= PURPLE_MIN else "red" if purple_share <= RED_MAX else None
    style = "red" if gold >= ORNAMENT_MIN else "purple" if gold <= ORNAMENT_ABSENT_MAX else None
    if colour is None:
        return Read(None, 0.0, f"mixed background colour ({cues})")
    if style != colour:
        return Read(None, 0.0, f"background colour and ornament disagree ({cues})")
    confidence = purple_share if colour == "purple" else 1 - purple_share
    return Read(colour, round(confidence, 3), cues)


def _row_ink(image: BgrImage, edge: float, cy: float, unit: float) -> float:
    """Share of light, unsaturated pixels clearly brighter than the cell background (a value cell's text)."""
    cell, _origin = _crop(image, _window_box(INK_WINDOW, edge, cy, unit))
    if cell.size == 0:
        return 0.0
    _hue, saturation, value = _hsv(cell)
    return float(((value > np.median(value) + INK_CONTRAST) & (saturation < INK_SATURATION_MAX)).mean())


# ===================================================================================================== checks


def _check_average(average: Read[int], pieces: Mapping[GearSlot, GearPiece], warnings: list[str]) -> None:
    """Average = floor(sum of the 6 scores / 6) held on 5 of 5 captures (status assumed): a warning, never a fix."""
    scores = [p.score.value for p in pieces.values() if p.score.value is not None]
    if average.value is None or len(scores) != len(GearSlot):
        return
    expected = sum(scores) // len(scores)
    if expected != average.value:
        warnings.append(
            f"average score {average.value} != floor({sum(scores)}/6) = {expected} (assumed rule): "
            "a score may be misread"
        )


# ===================================================================================================== artifact


def _read_artifact(
    context: _Context,
    lines: Sequence[TextLine],
    anchor: TextLine,
    names: Mapping[str, str],
    texts: _Texts,
    warnings: list[str],
) -> ArtifactPanel | None:
    h = context.grid.unit
    found = [
        (ln, m)
        for ln in lines
        if (m := texts.level.fullmatch(ln.text.replace(" ", "")))
        and ln.box.x0 > anchor.box.x0
        and ln.box.y1 < anchor.box.y0
        and ln.box.y0 > anchor.box.y0 - ARTIFACT_ABOVE * h
    ]
    if not found:
        warnings.append("artifact not found (no 'Lv.' line above the score: none equipped, or OCR missed it) -> REVIEW")
        return None
    level_line, match = max(found, key=lambda item: item[0].box.y0)
    cap = match.group("cap")
    level_text = Read(
        level_line.text.replace(" ", ""),
        round(level_line.score, 3) if cap else ODD_FIRST_PASS,
        "ocr" if cap else "level cap not read (it is drawn over the artwork)",
    )
    name_line = _artifact_name(lines, level_line, h)
    name_text = name_line.text.strip() if name_line is not None else ""
    code = _match_artifact(name_line, names)
    enhance = _artifact_enhance(context, lines, level_line)
    current = match.group("current")
    level = int(current) if current.isdigit() else (int(cap) if cap else None)
    if enhance.value is not None and level is not None and 1 + enhance.value // ARTIFACT_LEVEL_STEP != level:
        warnings.append(
            f"artifact {level_text.value} vs +{enhance.value}: inconsistent (community rule Lv = 1 + floor(N/3), "
            "unverified) -> REVIEW"
        )
        level_text = Read(level_text.value, min(level_text.confidence, INCONSISTENT_LEVEL_TEXT), "inconsistent with +N")
        enhance = Read(enhance.value, min(enhance.confidence, INCONSISTENT_ENHANCE), f"{enhance.note}; inconsistent")
    for what, read in (("code", code), ("'+N'", enhance)):
        if read.value is None:
            warnings.append(f"artifact {what}: {read.note} -> REVIEW")
    boxes = [level_line.box] + ([name_line.box] if name_line is not None else [])
    return ArtifactPanel(code, name_text, enhance, level_text, _union(boxes))


def _artifact_name(lines: Sequence[TextLine], level_line: TextLine, h: float) -> TextLine | None:
    low, high = ARTIFACT_NAME_DY
    candidates = [
        ln
        for ln in lines
        if abs(ln.box.x0 - level_line.box.x0) < ARTIFACT_NAME_X * h
        and low * h < ln.box.cy - level_line.box.cy < high * h
        and _LETTERS.search(ln.text)
    ]
    return min(candidates, key=lambda ln: ln.box.cy) if candidates else None


def _match_artifact(name_line: TextLine | None, names: Mapping[str, str]) -> Read[str]:
    if name_line is None:
        return Read(None, 0.0, "name not read")
    text = name_line.text.strip()
    choice = match_label(text, names)
    if choice is None:
        return Read(None, 0.0, f"name {text!r} not recognised (exact or margin rule)")
    if normalise(choice) == normalise(text):
        return Read(names[choice], round(name_line.score, 3), "exact name")
    similarity = _similarity(text, choice)
    return Read(names[choice], round(min(name_line.score, similarity), 3), f"{text!r} ~ {choice!r} ({similarity:.2f})")


def _artifact_enhance(context: _Context, lines: Sequence[TextLine], level_line: TextLine) -> Read[int]:
    h = context.grid.unit
    x0, cy = level_line.box.x0, level_line.box.cy
    badges = [
        (ln, m)
        for ln in lines
        if (m := _BADGE.fullmatch(ln.text.strip()))
        and x0 + ARTIFACT_BADGE_X[0] * h < ln.box.x1 < x0 + ARTIFACT_BADGE_X[1] * h
        and cy + ARTIFACT_BADGE_Y[0] * h < ln.box.cy < cy + ARTIFACT_BADGE_Y[1] * h
    ]
    if badges:
        line, match = badges[0]
        return Read(int(match.group(1)), round(line.score, 3), "ocr")
    reads = [
        [m.group(0) for t in texts if (m := _PLUS_TAIL.search(t.replace(" ", "")))]
        for texts in _reread(context, x0, cy, ARTIFACT_RENDERINGS)
    ]
    voted = _vote_int(reads, r"\+\d{1,2}", "'+N'")
    if voted.value is not None:
        return voted
    crop, _origin = _crop(context.image, _window_box(ARTIFACT_PILL_WINDOW, x0, cy, h))
    pill = bright_red_share(crop, ARTIFACT_PILL_HUE_MAX)
    if pill < ARTIFACT_PILL_ABSENT_MAX:
        return Read(0, ARTIFACT_INFERRED_ZERO, f"no '+N' and no pill (red/orange share {pill:.3f})")
    return Read(None, 0.0, f"pill present (share {pill:.2f}) but '+N' unread; {voted.note}")


def _union(boxes: Sequence[Box]) -> Box:
    return Box(min(b.x0 for b in boxes), min(b.y0 for b in boxes), max(b.x1 for b in boxes), max(b.y1 for b in boxes))


# ===================================================================================================== EE


def _read_exclusive(
    lines: Sequence[TextLine],
    anchor: TextLine,
    artifact: ArtifactPanel | None,
    grid: _Grid,
    warnings: list[str],
) -> ExclusivePanel | None:
    """The EE value + name left of the artifact; other text left in that area is a warning (never a silent "no EE")."""
    h = grid.unit
    right_limit = artifact.box.x0 - EE_ARTIFACT_GAP * h if artifact is not None else anchor.box.x1
    zone = [
        ln
        for ln in lines
        if ln.box.y1 < anchor.box.y0
        and ln.box.y0 > anchor.box.y0 - EE_ABOVE * h
        and ln.box.x0 > anchor.box.x0 - EE_LEFT_MARGIN * h
        and ln.box.x1 < right_limit
    ]
    found: ExclusivePanel | None = None
    used: list[TextLine] = []
    for line in zone:
        match = _EE_VALUE.fullmatch(line.text.replace(" ", ""))
        name = _ee_name(zone, line, h) if match else None
        if match is None or name is None:
            continue
        number, percent = float(match.group(1)), bool(match.group(2))
        value = round(number / 100, 6) if percent else number
        text = f"{match.group(1)}{match.group(2)}"
        cy, p = line.box.cy, grid.pitch
        window = Box(
            line.box.x1 - ICON_WINDOW_LEFT * p,
            cy - ICON_WINDOW_HALF_HEIGHT * p,
            line.box.x1 - ICON_WINDOW_RIGHT * p,
            cy + ICON_WINDOW_HALF_HEIGHT * p,
        )  # the OCR box starts at the icon: its left edge is not a limit
        row = ValueRow(text, value, percent, line.box, window, "ee", round(line.score, 3))
        found = ExclusivePanel(row, name.text.strip(), _union([line.box, name.box]))
        used = [line, name]
        break
    stray = [ln.text for ln in zone if ln not in used and _ALNUM.search(ln.text)]
    if stray:
        what = (
            "besides the Exclusive Equipment" if found is not None else "but no Exclusive Equipment value + name read"
        )
        warnings.append(f"text in the Exclusive Equipment area {what}: {stray} -> REVIEW")
    return found


def _ee_name(zone: Sequence[TextLine], value_line: TextLine, h: float) -> TextLine | None:
    low, high = EE_NAME_DY
    candidates = [
        ln
        for ln in zone
        if abs(ln.box.x0 - value_line.box.x0) < EE_NAME_X * h
        and low * h < ln.box.cy - value_line.box.cy < high * h
        and _LETTERS.search(ln.text)
    ]
    return min(candidates, key=lambda ln: ln.box.cy) if candidates else None


# ===================================================================================================== no anchor


def _absent(image: BgrImage, lines: Sequence[TextLine], texts: _Texts | None, warnings: list[str]) -> GearPanel:
    """No anchor: say so when the right half of the screen still shows gear-like text."""
    side = GEAR_SIDE * image.shape[1]
    gear_like = [
        ln.text
        for ln in lines
        if ln.box.x0 >= side
        and (
            _value_text(ln.text) is not None
            or (texts is not None and texts.level.fullmatch(ln.text.replace(" ", "")) is not None)
        )
    ]
    if len(gear_like) >= GEAR_LIKE_MIN:
        warnings.append(f"gear panel not found although gear-like text is present: {gear_like[:6]} -> REVIEW")
    return GearPanel(
        present=False,
        anchor=None,
        average_score=Read(None, 0.0, "no 'Average Equipment Score' line"),
        unit=0.0,
        pieces={},
        artifact=None,
        exclusive=None,
        warnings=tuple(warnings),
    )
