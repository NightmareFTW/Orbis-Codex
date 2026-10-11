"""Passive watch (M9a, SPEC D45 path B, D54): decide when the game shows a new, settled screen worth reading.

Pure image comparison of small greyscale thumbnails: no OCR, no input to the game. A screen is read once no big
change happened for `settle` seconds (the game rolls the stat numbers up and fades the bonus in after a hero switch:
small changes the thumbnail cannot see, so time is what lets them finish) and it differs from the last screen read.
Threshold and settle time are `assumed` (user's PC, 2026-10-11: reading at the first equal frame caught rolling
numbers such as "speed 2"; an idle hero animation stays below the threshold).
"""

from __future__ import annotations

from typing import Final

import numpy as np
from numpy.typing import NDArray

THUMB_SIZE: Final = (64, 36)
"""Width, height of the comparison thumbnail (16:9, like the game window)."""
DEFAULT_THRESHOLD: Final = 0.04
"""Mean absolute difference (0..1) above which two thumbnails are different screens (`assumed`)."""
DEFAULT_SETTLE: Final = 1.5
"""Seconds without a big change before a screen is read (`assumed`: lets number roll-ups and fades finish)."""


def thumbnail(image: NDArray[np.uint8]) -> NDArray[np.float32]:
    """Greyscale thumbnail (0..1) of a BGR or RGB image, by block averaging (no OpenCV needed)."""
    grey = image.astype(np.float32).mean(axis=2) / 255.0 if image.ndim == 3 else image.astype(np.float32) / 255.0
    width, height = THUMB_SIZE
    rows = np.linspace(0, grey.shape[0], height + 1).astype(int)
    cols = np.linspace(0, grey.shape[1], width + 1).astype(int)
    out = np.empty((height, width), dtype=np.float32)
    for i in range(height):
        band = grey[rows[i] : max(rows[i + 1], rows[i] + 1)]
        for j in range(width):
            out[i, j] = band[:, cols[j] : max(cols[j + 1], cols[j] + 1)].mean()
    return out


def differs(a: NDArray[np.float32], b: NDArray[np.float32], threshold: float = DEFAULT_THRESHOLD) -> bool:
    return a.shape != b.shape or float(np.abs(a - b).mean()) > threshold


class ScreenWatcher:
    """Feed one thumbnail per capture with the time; `feed` says when the screen is new and settled (read it now).

    A change is measured against the screen as it was at the last change (so a slow fade adds up), and only a change
    above the threshold restarts the settle time."""

    def __init__(self, threshold: float = DEFAULT_THRESHOLD, settle: float = DEFAULT_SETTLE) -> None:
        self.threshold = threshold
        self.settle = settle
        self._reference: NDArray[np.float32] | None = None
        self._changed_at = 0.0
        self._read: NDArray[np.float32] | None = None

    def feed(self, thumb: NDArray[np.float32], now: float) -> bool:
        if self._reference is None or differs(self._reference, thumb, self.threshold):
            self._reference, self._changed_at = thumb, now
        if now - self._changed_at < self.settle:
            return False
        if self._read is not None and not differs(self._read, thumb, self.threshold):
            return False
        self._read = thumb
        return True
