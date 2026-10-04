"""OCR engine behind a small interface, so screen parsers are tested with synthetic text and no model.

Engine: RapidOCR (PP-OCR models inside the wheel, ONNX Runtime, offline), chosen as the leading candidate in the
research (ARCHITECTURE "OCR"); confirmed on the user's first captures (M5b notes in docs/ROADMAP.md).
OCR scores are not trusted on their own: every parsed field is also checked (formats, catalog, base-stat sums).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Final, Protocol

INTRA_OP_THREADS: Final = 4
"""Keep inference from taking every core while the game runs."""


@dataclass(frozen=True, slots=True)
class Box:
    """Axis-aligned box in image pixels."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    score: float
    box: Box


@dataclass(frozen=True, slots=True)
class TextLine:
    text: str
    score: float
    box: Box
    words: tuple[Word, ...]


class TextReader(Protocol):
    def read(self, image: Any) -> list[TextLine]: ...


def _box(points: Sequence[Sequence[float]]) -> Box:
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return Box(min(xs), min(ys), max(xs), max(ys))


class RapidOcrReader:
    """Text detection + recognition with per-word boxes. The engine is created on first use (model load ~1 s)."""

    @cached_property
    def _engine(self) -> Any:
        from rapidocr import RapidOCR

        return RapidOCR(
            params={
                "Global.log_level": "error",
                "Global.use_cls": False,  # game text is never rotated
                "Global.text_score": 0.0,  # keep every result; our own checks decide
                "EngineConfig.onnxruntime.intra_op_num_threads": INTRA_OP_THREADS,
            }
        )

    def read(self, image: Any) -> list[TextLine]:
        result = self._engine(image, return_word_box=True)
        lines: list[TextLine] = []
        if result.txts is None:
            return lines
        word_results = result.word_results or [()] * len(result.txts)
        for points, text, score, words in zip(result.boxes, result.txts, result.scores, word_results, strict=True):
            parsed = tuple(Word(str(w[0]), float(w[1]), _box(w[2])) for w in words or ())
            lines.append(TextLine(str(text), float(score), _box(points), parsed))
        return lines
