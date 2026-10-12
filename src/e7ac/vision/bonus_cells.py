"""Re-read the orange "▲" bonus of Equipment-tab stat rows from a tight crop of its cell (SPEC D57).

Why: on live frames the orange bonus sits on bright red/pink hero art (sometimes under it), so the full-frame OCR
misses it or misreads a digit while the white final value is read right (user's watch captures, 2026-10-12). A small
cell holding only the bonus, given to the recogniser alone (no text detection), reads it far better: the recogniser
scales the cell to a fixed height, so a tight cell means large glyphs.

Measured with `spikes/watch_ocr_eval.py` on the user's 8 Equipment captures x 9 perturbations: the full-frame reading
gets 82% of the rows fully right; with this re-read, 100% and no wrong value accepted (the out-of-sample variants are
in SPEC D57). Positions come only from the anchors (label boxes, the bonus words read on other rows), never pixels.

The caller decides which candidate is right (`accept`, the catalog base check of MECH-STAT-06): this module never
sees the base, so the bonus can never be derived from it, and every row gets the same cells in the same order.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final

import cv2
import numpy as np
from numpy.typing import NDArray

from e7ac.domain.codes import Stat
from e7ac.vision.hero_screen import FLAT_STATS, PANEL_WIDTH_ROWS, ROW_TOLERANCE, HeroScreenReading
from e7ac.vision.ocr import Box, CellReader, TextLine, Word

Image = NDArray[np.uint8]
Accept = Callable[[Stat, float | None], bool]
"""accept(stat, bonus): True when that bonus (None: no bonus, i.e. zero) is the right one for the row."""

_NUMBER: Final = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
_RATE: Final = re.compile(r"(?P<int>\d+)(?:[.,](?P<dec>\d))?(?P<pct>%?)")
"""A rate cell: '111.0%'. Lossy captures drop the thin '%' or turn the '.' into ',' ('147.0', '6,0%'): both are
taken (a rate cell holds nothing else and rates never use a thousands comma), but a token needs its '%' or its one
decimal, so bare digits ('1470') are never re-cut into a percentage."""

BONUS_GAP_ROWS: Final = 1.15
"""When no row has a bonus word to locate the bonus column: it starts this many row heights after the final values
(the "▲" sits in between; within 0.13 rows of the measured column on the user's captures)."""
CROP_LEFT_ROWS: Final = 0.25
CROP_RIGHT_ROWS: Final = 3.0
CROP_HALF_HEIGHT_ROWS: Final = 0.4
"""The cell: from a quarter row left of the bonus column to three rows right of it (the widest bonus, 5 digits or
"111.0%", is ~2.2 rows), 0.4 rows above and below the label's centre. Tight on purpose: less background means larger
glyphs, so the thin "." and "%" survive lossy frames (0.6 rows: 212/234 cells read; 0.4 rows: 351/351)."""
PAD_CELL: Final = 0.2
"""White margin around the cell, in cell heights: the recogniser reads a glyph touching the edge poorly."""


def _stretch(feature: NDArray[np.int16]) -> Image:
    """Scale a non-negative feature so its brightest glyph pixels reach white (robust to darker/brighter frames)."""
    top = float(np.percentile(feature, 99.5))
    return np.asarray(np.clip(feature.astype(np.float32) * (255.0 / max(top, 1.0)), 0, 255), dtype=np.uint8)


def green_minus_blue(cell: Image) -> Image:
    """Orange glyphs (G > B) bright; red/pink/purple art (G <= B) and white text (G ~ B) black."""
    blue, green, _ = cv2.split(cell.astype(np.int16))
    return _stretch(np.clip(green - blue, 0, None))


def _as_is(cell: Image) -> Image:
    return cell


def _doubled(cell: Image) -> Image:
    return np.asarray(cv2.resize(cell, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC), dtype=np.uint8)


CELL_IMAGES: Final[tuple[tuple[str, Callable[[Image], Image]], ...]] = (
    ("colour", _as_is),
    ("g-b", green_minus_blue),
    ("colour x2", _doubled),
)
"""Tried in this order on each row still unsettled (one batched recognition per image). G - B isolates orange best of
the transforms measured (HSV window, Otsu, gamma, G - kB mixes, ...) and reads the digit under Kawerik's art; but it
is chroma, which lossy frames keep at half resolution, so the thin "." and "%" fade ('9.0%' read '9.0'). The colour
cell keeps the luma detail and reads the most; G - B and the enlarged cell are independent second and third tries."""


@dataclass(frozen=True, slots=True)
class BonusReread:
    stat: Stat
    bonus: float | None
    """The bonus read in the cell; None when the cell shows no number (no bonus: zero)."""
    image: str
    """Which cell image gave it (CELL_IMAGES)."""
    text: str
    score: float


def reread_bonuses(
    image: Image,
    reading: HeroScreenReading,
    lines: Sequence[TextLine],
    reader: CellReader,
    stats: Iterable[Stat],
    accept: Accept,
) -> dict[Stat, BonusReread]:
    """The first accepted bonus per row of `stats`, read from the row's bonus cell; rows with none are left out."""
    layout = _layout(reading, lines)
    if layout is None:
        return {}
    cells = {}
    for stat in stats:
        label = reading.anchors.labels.get(stat)
        cell = _cell(image, label, layout) if label is not None else None
        if cell is not None:
            cells[stat] = cell
    found: dict[Stat, BonusReread] = {}
    pending = list(cells)
    for name, transform in CELL_IMAGES:
        if not pending:
            break
        texts = reader.recognise([_framed(transform(cells[stat])) for stat in pending])
        for stat, (text, score) in zip(pending, texts, strict=True):
            tokens = _NUMBER.findall(text.replace(" ", ""))
            bonus = parse_value(tokens[0], stat) if tokens else None
            if (bonus is not None or not tokens) and accept(stat, bonus):
                found[stat] = BonusReread(stat, bonus, name, text, round(score, 3))
        pending = [stat for stat in pending if stat not in found]
    return found


def parse_value(token: str, stat: Stat) -> float | None:
    """A cell's number in its row's format: flat '2374' (never '%' or '.'), rates as in _RATE; anything else None."""
    if stat in FLAT_STATS:
        return None if ("%" in token or "." in token) else float(token.replace(",", ""))
    match = _RATE.fullmatch(token)
    if match is None or not (match["pct"] or match["dec"]):
        return None
    return round(float(f"{match['int']}.{match['dec'] or 0}") / 100, 6)


@dataclass(frozen=True, slots=True)
class _Layout:
    row_height: float
    bonus_left: float
    """x where the bonus column starts (left edge of the bonus digits)."""


def _layout(reading: HeroScreenReading, lines: Sequence[TextLine]) -> _Layout | None:
    """Row height = median label height; bonus column = median left edge of the second number read on each row (the
    game left-aligns that column; the median ignores a "▲" read as "-" or a stray word), else right of the finals."""
    labels = reading.anchors.labels
    if not labels:
        return None
    words = [w for line in lines for w in (line.words or (Word(line.text, line.score, line.box),))]
    row_height = statistics.median(box.height for box in labels.values())
    right = min(box.x0 for box in labels.values()) + PANEL_WIDTH_ROWS * row_height
    bonus_lefts: list[float] = []
    final_ends: list[float] = []
    for label in labels.values():
        numbers = _row_numbers(label, words, right)
        if numbers and len(_NUMBER.findall(numbers[0].text)) == 1:  # not a merged "13517-8218"
            final_ends.append(numbers[0].box.x1)
        if len(numbers) >= 2:
            bonus_lefts.append(numbers[1].box.x0)
    if bonus_lefts:
        return _Layout(row_height, statistics.median(bonus_lefts))
    if final_ends:
        return _Layout(row_height, statistics.median(final_ends) + BONUS_GAP_ROWS * row_height)
    return None


def _row_numbers(label: Box, words: Sequence[Word], right: float) -> list[Word]:
    """Number words right of a label on its row, left to right (as the parser assigns them)."""
    tolerance = ROW_TOLERANCE * label.height
    on_row = (
        w
        for w in words
        if abs(w.box.cy - label.cy) <= tolerance and label.x1 < w.box.x0 < right and _NUMBER.search(w.text)
    )
    return sorted(on_row, key=lambda w: w.box.x0)


def _cell(image: Image, label: Box, layout: _Layout) -> Image | None:
    h = layout.row_height
    x0 = max(0, round(layout.bonus_left - CROP_LEFT_ROWS * h))
    x1 = min(image.shape[1], round(layout.bonus_left + CROP_RIGHT_ROWS * h))
    y0 = max(0, round(label.cy - CROP_HALF_HEIGHT_ROWS * h))
    y1 = min(image.shape[0], round(label.cy + CROP_HALF_HEIGHT_ROWS * h))
    if x1 - x0 < h or y1 - y0 < h / 2:
        return None
    return image[y0:y1, x0:x1]


def _framed(cell: Image) -> Image:
    """The recogniser's input: 3 channels (a grey isolation inverted, dark glyphs on white) with a white margin."""
    if cell.ndim == 2:
        cell = np.asarray(cv2.cvtColor(255 - cell, cv2.COLOR_GRAY2BGR), dtype=np.uint8)
    pad = max(2, round(PAD_CELL * cell.shape[0]))
    framed = cv2.copyMakeBorder(cell, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    return np.asarray(framed, dtype=np.uint8)
