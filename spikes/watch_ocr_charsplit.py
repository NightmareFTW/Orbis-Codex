"""Spike reader for `watch_ocr_eval.py`: RapidOCR with one box per character on every line.

On the user's PC RapidOCR split some stat rows into single characters (a line taken for Chinese, the "▲" read as a
CJK glyph), and "229▲108" was read as speed 2. Forcing that mode on every line checks that the parser glues the
digits back (`hero_screen.GLUE_GAP`): this reader should score like the normal one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from e7ac.vision.hero_screen import parse_hero_screen
from e7ac.vision.ocr import TextLine, Word, _box


def read_lines(image: Any, reader: Any) -> list[TextLine]:
    result = reader._engine(image, return_word_box=True, return_single_char_box=True)
    lines: list[TextLine] = []
    if result.txts is None:
        return lines
    word_results = result.word_results or [()] * len(result.txts)
    for points, text, score, words in zip(result.boxes, result.txts, result.scores, word_results, strict=True):
        parsed = tuple(Word(str(w[0]), float(w[1]), _box(w[2])) for w in words or ())
        lines.append(TextLine(str(text), float(score), _box(points), parsed))
    return lines


def read_rows(image: Any, reader: Any, base: Mapping[str, float]) -> dict[str, tuple[float | None, float | None]]:
    reading = parse_hero_screen(read_lines(image, reader))
    return {stat.value: (row.final, row.bonus) for stat, row in reading.stats.items()}
