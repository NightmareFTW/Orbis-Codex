"""Read a hero screen (Equipment tab with "▲" bonuses, or Hero Info) from OCR text lines.

Positions are never hard-coded in pixels: the nine stat labels are the anchors, values are the words to the right of
each label on the same row. Every field keeps the raw text and a confidence; nothing is guessed silently.
Layout facts come from the user's captures (Stove PC, English, 2026-10-04):
- stat rows in a fixed order with the final value, then (Equipment tab only) an orange "▲" bonus;
- "Lv. Max/60" or "Lv. 52/60" above the imprint text and the CP ("123,120"); the imprint text wraps on up to three
  left-aligned lines ("Effect" / "Resistance +" / "15%") or reads "Locked" when the hero has none (MECH-IMP-02);
- active set names ("Speed Set") under the stats.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from e7ac.domain.codes import Stat
from e7ac.settings import GameLanguage
from e7ac.vision.labels import IMPRINT_LOCKED, LEVEL_PREFIX, NO_SET_EFFECT, STAT_LABELS, match_label, normalise
from e7ac.vision.ocr import Box, TextLine, Word

FLAT_STATS: Final = frozenset({Stat.ATK, Stat.DEF, Stat.HP, Stat.SPEED})
_NUMBER: Final = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
_CP: Final = re.compile(r"^\d{1,3}(?:,\d{3})+$|^\d{4,7}$")
_LEVEL: Final = re.compile(r"(max|\d{1,2})\s*/\s*(\d{2})", re.IGNORECASE)
_IMPRINT: Final = re.compile(r"^(?P<label>[^\d+]+?)\s*\+\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<pct>%?)$")
_PERCENT_VERSION: Final = {Stat.ATK: Stat.ATK_PERCENT, Stat.DEF: Stat.DEF_PERCENT, Stat.HP: Stat.HP_PERCENT}


class ScreenKind(StrEnum):
    EQUIPMENT = "equipment"
    """Hero > Equipment tab: final value plus "▲" bonus (= final - base) per stat."""
    HERO_INFO = "hero_info"
    """Hero Info: final values only (plus gear details, read separately)."""


class ScreenError(Exception):
    """The image is not a hero screen we can read (no stat rows found)."""


@dataclass(frozen=True, slots=True)
class StatReading:
    stat: Stat
    final: float | None
    """Flat stats in game units; rates as fractions (36.0% -> 0.36)."""
    bonus: float | None
    raw: str
    confidence: float


@dataclass(slots=True)
class ScreenAnchors:
    """Boxes of the texts the panel was read from: the image readers (icons, stars) work relative to them."""

    labels: dict[Stat, Box] = field(default_factory=dict)
    """Stat label boxes; their icon sits left of each label (the stat-icon templates, M7)."""
    name: Box | None = None
    level: Box | None = None
    imprint: tuple[Box, ...] = ()
    """The imprint text block (one box per wrapped line), or the "Locked" block."""
    cp: Box | None = None


@dataclass(slots=True)
class HeroScreenReading:
    kind: ScreenKind
    name: str | None = None
    name_confidence: float = 0.0
    level: int | None = None
    level_cap: int | None = None
    imprint_stat: Stat | None = None
    imprint_value: float | None = None
    imprint_raw: str = ""
    imprint_locked: bool = False
    """The screen says "Locked": the hero has no imprint."""
    cp: int | None = None
    stats: dict[Stat, StatReading] = field(default_factory=dict)
    set_names: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    anchors: ScreenAnchors = field(default_factory=ScreenAnchors)


def parse_hero_screen(lines: Sequence[TextLine], language: GameLanguage = GameLanguage.EN) -> HeroScreenReading:
    labels = STAT_LABELS[language]
    if not labels:
        raise ScreenError(
            f"no screen texts are known yet for game language '{language.value}' - send a capture in that language "
            "so its labels can be added (SPEC D39)"
        )
    column = _stat_column(lines, labels)
    if column is None:
        raise ScreenError("not a hero screen: the stat rows were not found")
    reading = HeroScreenReading(kind=ScreenKind.HERO_INFO)
    for stat, row in column.rows.items():
        reading.stats[stat] = _stat_values(stat, row, column)
        reading.anchors.labels[stat] = row.box
    missing = [s.value for s in labels.values() if s not in reading.stats]
    if missing:
        reading.warnings.append(f"stat rows not found: {', '.join(missing)}")
    if sum(1 for r in reading.stats.values() if r.bonus is not None) >= 3:
        reading.kind = ScreenKind.EQUIPMENT
    panel = [ln for ln in lines if column.contains(ln) and ln not in column.rows.values()]
    level_line = _read_level(reading, panel, column.top, language)
    reading.anchors.level = level_line.box if level_line is not None else None
    upper_limit = level_line.box.y1 if level_line is not None else 0.0
    _read_cp(reading, panel, upper_limit, column)
    _read_imprint(reading, panel, upper_limit, column.top, language)
    _read_name(reading, panel, level_line)
    _read_sets(reading, panel, column.bottom, language)
    return reading


@dataclass(frozen=True, slots=True)
class _Column:
    """The stat label column: the anchor for every other field of the panel."""

    rows: dict[Stat, TextLine]
    x0: float
    row_height: float
    words: tuple[Word, ...]

    @property
    def top(self) -> float:
        return min(line.box.y0 for line in self.rows.values())

    @property
    def bottom(self) -> float:
        return max(line.box.y1 for line in self.rows.values())

    @property
    def right(self) -> float:
        """Right end of the panel: labels and values never go further than this."""
        return self.x0 + PANEL_WIDTH_ROWS * self.row_height

    def contains(self, line: TextLine) -> bool:
        return self.x0 - 3 * self.row_height <= line.box.x0 < self.right


PANEL_WIDTH_ROWS: Final = 14.0
"""Width of the stats panel (labels + final value + "▲" bonus) in row heights: ~12.5-13 on the user's captures."""


def _all_words(lines: Sequence[TextLine]) -> tuple[Word, ...]:
    return tuple(w for line in lines for w in (line.words or (Word(line.text, line.score, line.box),)))


def _stat_column(lines: Sequence[TextLine], labels: Mapping[str, Stat]) -> _Column | None:
    """Exact label matches fix the column (left edge and row height); fuzzy matches are only accepted on it."""
    exact: dict[Stat, TextLine] = {}
    by_norm = {normalise(text): stat for text, stat in labels.items()}
    for line in lines:
        stat = by_norm.get(normalise(line.text))
        if stat is not None and stat not in exact:
            exact[stat] = line
    if len(exact) < 3:
        return None
    lefts = sorted(line.box.x0 for line in exact.values())
    heights = sorted(line.box.height for line in exact.values())
    x0, row_height = lefts[len(lefts) // 2], heights[len(heights) // 2]
    rows = {stat: line for stat, line in exact.items() if abs(line.box.x0 - x0) <= row_height}
    for line in lines:
        if abs(line.box.x0 - x0) > row_height / 2 or line in rows.values():
            continue
        text = line.text
        cut = next((i for i, w in enumerate(line.words) if _NUMBER.search(w.text)), None)
        if cut:  # label and value merged in one detected line ("Attack 1800")
            text = " ".join(w.text for w in line.words[:cut])
        match = match_label(text, labels)
        if match is not None and labels[match] not in rows:
            rows[labels[match]] = line
    if len(rows) < 5:
        return None
    return _Column(rows=rows, x0=x0, row_height=row_height, words=_all_words(lines))


def _parse_number(text: str, stat: Stat) -> tuple[float | None, float]:
    """(value, confidence): rates must look like '36.0%', flat stats like '13517' (no % ever)."""
    match = _NUMBER.search(text.replace(" ", ""))
    if match is None:
        return None, 0.0
    token = match.group(0)
    if stat in FLAT_STATS:
        if token.endswith("%") or "." in token:
            return None, 0.0
        return float(token.replace(",", "")), 1.0
    if not token.endswith("%"):
        return None, 0.0
    number = token[:-1].replace(",", "")
    confidence = 1.0 if re.fullmatch(r"\d+\.\d", number) else 0.6  # the game shows one decimal
    return round(float(number) / 100, 6), confidence


ROW_TOLERANCE: Final = 0.4
"""A value belongs to a stat row when its vertical centre is within this many label heights of the label's centre
(rows are one label height apart, so neighbouring rows never qualify)."""


def _stat_values(stat: Stat, label: TextLine, column: _Column) -> StatReading:
    tolerance = ROW_TOLERANCE * label.box.height
    own = [w for w in label.words if _NUMBER.search(w.text)]  # merged "Attack 1800" lines
    others = [
        w
        for w in column.words
        if abs(w.box.cy - label.box.cy) <= tolerance and label.box.x1 < w.box.x0 < column.right and w not in label.words
    ]
    words = sorted([*own, *others], key=lambda w: w.box.x0)
    # numbers in reading order: final value, then the "▲" bonus; other glyphs (the "▲" itself) are skipped
    numbers = [(word, *_parse_number(word.text, stat)) for word in words if _NUMBER.search(word.text)][:2]
    raw = " ".join(w.text for w, _, _ in numbers)
    if not numbers:
        return StatReading(stat, None, None, raw, 0.0)
    first_word, final, final_conf = numbers[0]
    bonus = numbers[1][1] if len(numbers) > 1 else None
    confidence = min(final_conf, first_word.score, label.score)
    return StatReading(stat, final, bonus, raw, round(confidence, 3))


def _read_level(
    reading: HeroScreenReading, panel: list[TextLine], top: float, language: GameLanguage
) -> TextLine | None:
    prefix = normalise(LEVEL_PREFIX.get(language, "Lv.")).rstrip(".")
    for line in sorted(panel, key=lambda ln: ln.box.y0):
        if line.box.y1 > top or not normalise(line.text).startswith(prefix):
            continue
        match = _LEVEL.search(line.text)
        if match is None:
            reading.warnings.append(f"level not readable: {line.text!r}")
            return line
        cap = int(match.group(2))
        level = cap if match.group(1).casefold() == "max" else int(match.group(1))
        if not 1 <= level <= cap <= 60:
            reading.warnings.append(f"level not plausible: {line.text!r}")
            return line
        reading.level, reading.level_cap = level, cap
        return line
    reading.warnings.append("level line not found")
    return None


def _read_cp(reading: HeroScreenReading, panel: list[TextLine], upper: float, column: _Column) -> None:
    candidates = [
        ln
        for ln in panel
        if upper <= ln.box.y0
        and ln.box.y1 <= column.top
        and ln.box.x0 < column.x0 + 4 * column.row_height
        and _CP.match(ln.text.strip())
    ]
    if not candidates:
        reading.warnings.append("CP not found")
        return
    best = max(candidates, key=lambda ln: ln.box.y0)  # the CP sits right above the stat rows
    reading.cp = int(best.text.strip().replace(",", ""))
    reading.anchors.cp = best.box


def _read_imprint(
    reading: HeroScreenReading, panel: list[TextLine], upper: float, top: float, language: GameLanguage
) -> None:
    labels = STAT_LABELS[language]
    region = sorted((ln for ln in panel if upper <= ln.box.y0 and ln.box.y1 <= top), key=lambda ln: ln.box.y0)
    locked = normalise(IMPRINT_LOCKED.get(language, ""))
    locked_line = next((ln for ln in region if locked and normalise(ln.text) == locked), None)
    if locked_line is not None:
        reading.imprint_locked = True
        reading.anchors.imprint = tuple(ln.box for ln in _text_block(locked_line, region))
        return
    for line in (ln for ln in region if "+" in ln.text):
        block = _text_block(line, region)
        text = " ".join(ln.text.strip() for ln in block)
        match = _IMPRINT.match(text)
        if match is None:
            continue
        label = match_label(match.group("label"), labels)
        if label is None:
            continue
        stat = labels[label]
        value = float(match.group("value"))
        if match.group("pct"):
            reading.imprint_stat = _PERCENT_VERSION.get(stat, stat)
            reading.imprint_value = round(value / 100, 6)
        elif stat in FLAT_STATS:
            reading.imprint_stat, reading.imprint_value = stat, value
        else:
            reading.warnings.append(f"imprint without % for a rate stat: {text!r}")
            return
        reading.imprint_raw = text
        reading.anchors.imprint = tuple(ln.box for ln in block)
        return
    reading.warnings.append("imprint not found (neither a value nor 'Locked')")


def _text_block(line: TextLine, region: list[TextLine]) -> list[TextLine]:
    """`line` with the left-aligned lines wrapped around it: label words above, the value below a trailing "+"
    ("Effect" / "Resistance +" / "15%"). Wrapped boxes may overlap a little."""

    def next_to(a: TextLine, b: TextLine) -> bool:  # b directly below a, same left edge
        return -0.5 * a.box.height <= b.box.y0 - a.box.y1 < a.box.height and abs(b.box.x0 - a.box.x0) < a.box.height

    block = [line]
    while True:
        above = [
            ln
            for ln in region
            if ln not in block
            and next_to(ln, block[0])
            and "+" not in ln.text
            and not any(c.isdigit() for c in ln.text)
        ]
        if not above:
            break
        block.insert(0, max(above, key=lambda ln: ln.box.y1))
    if line.text.strip().endswith("+"):
        below = [ln for ln in region if ln not in block and next_to(line, ln)]
        if below:
            block.append(min(below, key=lambda ln: ln.box.y0))
    return block


def _read_name(reading: HeroScreenReading, panel: list[TextLine], level_line: TextLine | None) -> None:
    """The hero name is the tallest mostly-alphabetic text above the level line."""
    limit = level_line.box.y0 if level_line is not None else None
    candidates = [
        ln
        for ln in panel
        if (limit is None or ln.box.y1 <= limit)
        and sum(c.isalpha() for c in ln.text) >= 3
        and len(set(ln.text.strip())) > 2  # star rows are read as 'AAAAA'
    ]
    if not candidates:
        reading.warnings.append("hero name not found")
        return
    best = max(candidates, key=lambda ln: ln.box.height)
    # star icons next to the name are sometimes read as symbols: keep letters, digits, spaces and ' & . -
    cleaned = re.sub(r"[^\w\s'&.-]", " ", best.text)
    reading.name = re.sub(r"\s+", " ", cleaned).strip()
    reading.anchors.name = best.box
    reading.name_confidence = round(best.score, 3)


def _read_sets(reading: HeroScreenReading, panel: list[TextLine], bottom: float, language: GameLanguage) -> None:
    none_text = normalise(NO_SET_EFFECT.get(language, ""))
    for line in sorted((ln for ln in panel if ln.box.y0 >= bottom), key=lambda ln: ln.box.y0):
        text = line.text.strip()
        if normalise(text) == none_text or not text.endswith("Set"):
            continue
        reading.set_names.append(text)
