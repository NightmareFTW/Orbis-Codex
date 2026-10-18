"""The Victory / Defeat screen that ends an Arena fight (SPEC D93): its big title, read from the top centre of the game
picture. Nothing else on the screen is needed to log the outcome; the Battle Results tabs are read separately."""

from __future__ import annotations

import re
from typing import Any, Final

from e7ac.vision.ocr import TextReader

CROP: Final = (0.12, 0.40, 0.30, 0.70)
"""The top, bottom, left and right of the title as shares of the picture (the title sits above the two teams)."""
_WORDS: Final = {
    "victory": "victory",
    "defeat": "defeat",
    "vitoria": "victory",  # the Portuguese client
    "derrota": "defeat",
}
MIN_SCORE: Final = 0.8


def read_result(image: Any, reader: TextReader) -> str | None:
    """ "victory", "defeat" or None when the picture's title area shows neither."""
    height, width = image.shape[:2]
    top, bottom, left, right = CROP
    crop = image[int(top * height) : int(bottom * height), int(left * width) : int(right * width)]
    for line in reader.read(crop):
        word = re.sub(r"[^a-z]", "", _plain(line.text).lower())
        if word in _WORDS and line.score >= MIN_SCORE:
            return _WORDS[word]
    return None


def _plain(text: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
