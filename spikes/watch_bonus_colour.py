"""Spike reader for `watch_ocr_eval.py`: re-read missed "▲" bonuses from a tight crop of their cell (as is, G - B).

Why: on the user's live frames the orange bonus sits on bright red/pink hero art (sometimes under it), so the
full-frame OCR misses it or misreads digits, while the white final value is read right. Orange text differs from that
art by its green-minus-blue: inside the bonus glyphs G - B reaches +35..+50 (95th percentile), on the red/pink/purple
background next to them it mostly stays at or below 0 (hero art up to ~+20), and white text has G - B ~ 0 (aggregate
statistics of the user's captures, 2026-10-12). How far that isolation carries, and where it fails: see ISOLATIONS.

How (cost stays small: nothing is added for rows the full-frame reading already gets right):
1. the app's own reading (`parse_hero_screen` on the full frame);
2. rows whose (final, bonus) fails the base check (MECH-STAT-06: final - bonus = catalog base) are re-read: the bonus
   cell is cropped next to the row's label (row height and the bonus column come from the anchors: the label boxes and
   the bonus words read on other rows) and passed to the recogniser alone (no text detection), as a few cell images
   (ISOLATIONS: the colour cell, its G - B isolation, the colour cell enlarged);
3. the base is only an arbiter: an OCR-produced pair is accepted when it passes the check; otherwise the full-frame
   reading is returned unchanged. The bonus is never computed from the base, and the base never steers the crop or
   the preprocessing (every row that fails gets the same cells, in the same order).
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import cv2
import numpy as np

from e7ac.domain.codes import Stat
from e7ac.vision.hero_screen import FLAT_STATS, HeroScreenReading, parse_hero_screen
from e7ac.vision.ocr import Box, TextLine, Word

Pair = tuple[float | None, float | None]

_NUMBER: Final = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
FLAT_TOLERANCE: Final = 0.5
RATE_TOLERANCE: Final = 0.0005
"""The harness's base check: flat stats within half a unit, rates within 0.05 percentage points."""

ROW_TOLERANCE: Final = 0.4
"""A word belongs to a row when its centre is within this many label heights of the label's centre (as the parser)."""
PANEL_WIDTH_ROWS: Final = 14.0
"""Right end of the stats panel in row heights from the label column (as the parser)."""
BONUS_GAP_ROWS: Final = 1.15
"""Fallback when no row has a second number: the bonus column starts this many row heights after the median right
end of the final values (the "▲" sits in between). On the 8 captures it lands within 0.13 rows of the measured column
and its cells read 62/62 bonuses; the median, not the max, so a merged "13517-8218" word cannot push it away."""
CROP_LEFT_ROWS: Final = 0.25
CROP_RIGHT_ROWS: Final = 3.0
CROP_HALF_HEIGHT_ROWS: Final = 0.4
"""Bonus cell: from a quarter row left of the bonus column (keeps the first digit whole, mostly clear of the "▲") to
three rows right of it (the widest bonus, 5 digits or "111.0%", is ~2.2 rows), and 0.4 rows above/below the label's
centre. A tight cell matters: the recogniser scales it to a fixed height, so less background means larger glyphs and
the thin "." and "%" survive (colour cell on the 5 live frames x 9 variants: 0.6 rows read 212/234 cells of
orig/blur/noise/shift/jpeg85/webp75, 0.45 rows 348/351 of all, 0.4 rows 351/351)."""
PAD_CELL: Final = 0.2
"""White margin around the cell, in cell heights: the recogniser reads a glyph touching the edge poorly."""


def passes(stat: Stat, final: float | None, bonus: float | None, base: float | None) -> bool:
    """The base check (arbiter only): final - bonus equals the catalog base; a missing bonus counts as 0."""
    if final is None or base is None:
        return False
    tolerance = FLAT_TOLERANCE if stat in FLAT_STATS else RATE_TOLERANCE
    return abs(final - (bonus or 0.0) - base) <= tolerance


# --- colour isolation -------------------------------------------------------------------------------------------


def _stretch(feature: np.ndarray) -> np.ndarray:
    """Scale a non-negative feature so its brightest glyph pixels reach white (robust to darker/brighter captures)."""
    top = float(np.percentile(feature, 99.5))
    scaled: np.ndarray = feature.astype(np.float32) * (255.0 / max(top, 1.0))
    return np.clip(scaled, 0, 255).astype(np.uint8)  # type: ignore[no-any-return]


def green_minus_blue(crop: np.ndarray) -> np.ndarray:
    """Orange glyphs (G > B) bright, red/pink/purple art (G <= B) and white text (G ~ B) black."""
    blue, green, _ = cv2.split(crop.astype(np.int16))
    return _stretch(np.clip(green - blue, 0, None))


def as_is(crop: np.ndarray) -> np.ndarray:
    """No isolation: the colour cell itself (see ISOLATIONS for why it is kept)."""
    return crop


def doubled(crop: np.ndarray) -> np.ndarray:
    """The colour cell at twice the size (bicubic): the recogniser resamples it differently, a third independent try."""
    return cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)


ISOLATIONS: Final[tuple[tuple[str, Callable[[np.ndarray], np.ndarray]], ...]] = (
    ("colour", as_is),
    ("g-b", green_minus_blue),
    ("colour x2", doubled),
)
"""Cell images tried in order on a failing row; the first reading that passes the base check wins (the order only sets
the cost: each try is one batched recognition of the rows still failing). G - B is the best colour isolation measured
(vs an HSV window, min(R-G, G-B), Otsu-binarised, gamma/percentile stretches, G - kB mixes, a per-cell LDA
projection, background whitening): it reads every bonus of the original captures, even the digit under Kawerik's art.
But every colour difference is chroma, and lossy captures (WebP/JPEG, 4:2:0) keep chroma at half resolution: the thin
"%" and "." lose their colour and vanish ('9.0%' read '9.0', '38.0%' read '380'). The colour cell keeps the luma
detail, so with a tight crop it reads more (all bonus cells of the 5 live frames x 9 variants: colour 351/351, G - B
337/351, colour x2 351/351); G - B stays as an independent second try for art the colour cell has not met yet."""


def clean(crop: np.ndarray, isolate: Callable[[np.ndarray], np.ndarray]) -> np.ndarray:
    """The recogniser's input, with a white margin, as a 3-channel image: grey isolations are inverted (dark glyphs
    on white, as the recogniser was trained)."""
    cell = isolate(crop)
    if cell.ndim == 2:
        cell = cv2.cvtColor(255 - cell, cv2.COLOR_GRAY2BGR)
    pad = max(2, round(PAD_CELL * cell.shape[0]))
    return cv2.copyMakeBorder(cell, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(255, 255, 255))


# --- geometry from the anchors ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Layout:
    row_height: float
    bonus_left: float
    """x where the bonus column starts (left edge of the bonus digits)."""


def _words(lines: Sequence[TextLine]) -> list[Word]:
    return [w for line in lines for w in (line.words or (Word(line.text, line.score, line.box),))]


def _row_numbers(label: Box, words: Sequence[Word], right: float) -> list[Word]:
    """Number words right of a label on its row, left to right."""
    tolerance = ROW_TOLERANCE * label.height
    return sorted(
        (
            w
            for w in words
            if abs(w.box.cy - label.cy) <= tolerance and label.x1 < w.box.x0 < right and _NUMBER.search(w.text)
        ),
        key=lambda w: w.box.x0,
    )


def _layout(reading: HeroScreenReading, words: Sequence[Word]) -> _Layout | None:
    """Row height = median label height; bonus column = median left edge of the second number read on each row (the
    bonus: the game left-aligns that column; the median ignores a "▲" read as "-" or a stray word), else right of the
    final values. Positions only: nothing here depends on the base."""
    labels = reading.anchors.labels
    if not labels:
        return None
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


def _bonus_cell(image: np.ndarray, label: Box, layout: _Layout) -> np.ndarray | None:
    h = layout.row_height
    x0 = max(0, round(layout.bonus_left - CROP_LEFT_ROWS * h))
    x1 = min(image.shape[1], round(layout.bonus_left + CROP_RIGHT_ROWS * h))
    y0 = max(0, round(label.cy - CROP_HALF_HEIGHT_ROWS * h))
    y1 = min(image.shape[0], round(label.cy + CROP_HALF_HEIGHT_ROWS * h))
    if x1 - x0 < h or y1 - y0 < h / 2:
        return None
    return image[y0:y1, x0:x1]


# --- recognition ------------------------------------------------------------------------------------------------


def recognise(reader: Any, images: Sequence[np.ndarray]) -> list[tuple[str, float]]:
    """(text, score) per image, by RapidOCR's recogniser alone, in one batch. Called directly rather than through
    `RapidOCR.__call__(use_det=False)`, which stores its options as engine state (the next full-frame read would skip
    detection) and is not safe beside the watch's background reader. Cells of one layout share a width/height ratio,
    so batching pads them alike and reads each as it would be read alone."""
    from rapidocr.ch_ppocr_rec import TextRecInput

    if not images:
        return []
    result = reader._engine.text_rec(TextRecInput(img=list(images), return_word_box=False))
    return [(str(text), float(score)) for text, score in zip(result.txts, result.scores, strict=True)]


_RATE: Final = re.compile(r"(?P<int>\d+)(?:[.,](?P<dec>\d))?(?P<pct>%?)")
"""A rate cell: '111.0%'. Lossy captures drop the thin '%' or turn the '.' into ',' ('147.0', '6,0%'): both are
taken (a rate cell holds nothing else, rates never use a thousands comma), but a token needs its '%' or its one
decimal, so digits alone ('1470') are never re-cut into a percentage."""


def parse_value(token: str, stat: Stat) -> float | None:
    """A number token in the row's own format: flat '2374' (no '%', no '.'), rates as in _RATE; anything else None."""
    if stat in FLAT_STATS:
        return None if ("%" in token or "." in token) else float(token.replace(",", ""))
    match = _RATE.fullmatch(token)
    if match is None or not (match["pct"] or match["dec"]):
        return None
    return round(float(f"{match['int']}.{match['dec'] or 0}") / 100, 6)


@dataclass(frozen=True, slots=True)
class BonusReading:
    """What one re-read gave: kept for diagnostics (which transform, raw text, OCR score)."""

    stat: Stat
    isolation: str
    text: str
    score: float
    bonus: float | None
    accepted: bool


def reread_bonuses(
    image: np.ndarray,
    reader: Any,
    reading: HeroScreenReading,
    lines: Sequence[TextLine],
    base: Mapping[Stat, float],
) -> tuple[dict[Stat, Pair], list[BonusReading]]:
    """(final, bonus) per row after re-reading the bonus of rows that fail the base check, plus every re-read.
    One batch per cell image (ISOLATIONS), only for the rows no earlier image settled; finals are never changed."""
    rows: dict[Stat, Pair] = {stat: (row.final, row.bonus) for stat, row in reading.stats.items()}
    ok = {stat: passes(stat, final, bonus, base.get(stat)) for stat, (final, bonus) in rows.items()}
    failing = [stat for stat, good in ok.items() if not good and rows[stat][0] is not None and stat in base]
    tried: list[BonusReading] = []
    layout = _layout(reading, _words(lines)) if failing else None
    if layout is None:
        return rows, tried
    cells: dict[Stat, np.ndarray] = {}
    for stat in failing:
        cell = _bonus_cell(image, reading.anchors.labels[stat], layout)
        if cell is not None:
            cells[stat] = cell
    pending = list(cells)
    for name, isolate in ISOLATIONS:
        if not pending:
            break
        texts = recognise(reader, [clean(cells[stat], isolate) for stat in pending])
        still: list[Stat] = []
        for stat, (text, score) in zip(pending, texts, strict=True):
            final = rows[stat][0]
            tokens = _NUMBER.findall(text.replace(" ", ""))
            # no digits at all in the cell is a reading too: the game shows no bonus (zero)
            bonus = parse_value(tokens[0], stat) if tokens else None
            accepted = (bonus is not None or not tokens) and passes(stat, final, bonus, base[stat])
            tried.append(BonusReading(stat, name, text, round(score, 3), bonus, accepted))
            if accepted:
                rows[stat] = (final, bonus)
            else:
                still.append(stat)
        pending = still
    return rows, tried


def read_rows(image: Any, reader: Any, base: Mapping[str, float]) -> dict[str, Pair]:
    """Harness entry point (`watch_ocr_eval.py`): stat value -> (final, bonus)."""
    lines = reader.read(image)
    reading = parse_hero_screen(lines)
    rows, _ = reread_bonuses(image, reader, reading, lines, {Stat(k): v for k, v in base.items()})
    return {stat.value: pair for stat, pair in rows.items()}
