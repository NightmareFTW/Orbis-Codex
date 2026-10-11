"""Passive watch (M9a, SPEC D45 path B, D54): decide when the game shows a new, settled screen worth reading.

Pure image comparison of small greyscale thumbnails: no OCR, no input to the game. A frame is read once it equals the
previous frame (the screen stopped changing) and differs from the last frame that was read. The threshold is
`assumed` (to be tuned on the user's PC: the Hero Info hero model has an idle animation).
"""

from __future__ import annotations

from typing import Final

import numpy as np
from numpy.typing import NDArray

THUMB_SIZE: Final = (64, 36)
"""Width, height of the comparison thumbnail (16:9, like the game window)."""
DEFAULT_THRESHOLD: Final = 0.04
"""Mean absolute difference (0..1) above which two thumbnails are different screens (`assumed`)."""


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
    """Feed one thumbnail per capture; `feed` says when the screen is new and settled (read it now)."""

    def __init__(self, threshold: float = DEFAULT_THRESHOLD) -> None:
        self.threshold = threshold
        self._previous: NDArray[np.float32] | None = None
        self._read: NDArray[np.float32] | None = None

    def feed(self, thumb: NDArray[np.float32]) -> bool:
        settled = self._previous is not None and not differs(self._previous, thumb, self.threshold)
        self._previous = thumb
        if not settled or (self._read is not None and not differs(self._read, thumb, self.threshold)):
            return False
        self._read = thumb
        return True
