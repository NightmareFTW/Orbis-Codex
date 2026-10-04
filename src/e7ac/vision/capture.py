"""Capture the game picture (client area of its window) and save it as PNG.

Backends sit behind `CaptureBackend` so OCR and tests never depend on Windows. M5a ships `mss` (GDI copy of the
visible screen area under the game window: the game must be visible and on top). Windows.Graphics.Capture comes with
the overlay (M9), which needs a capture that ignores windows on top of the game (SPEC D37).
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

from e7ac.vision.window import Rect, WindowInfo

FLAT_FRAME_STDEV: Final = 2.0
"""Pixel standard deviation below which a frame is considered blank (black/flat): a blocked or covered capture."""


class CaptureError(Exception):
    """The picture could not be captured."""


@dataclass(frozen=True, slots=True)
class Frame:
    width: int
    height: int
    rgb: bytes
    """Packed 8-bit RGB, row-major, no padding."""
    backend: str
    window: WindowInfo
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if len(self.rgb) != self.width * self.height * 3:
            raise CaptureError(f"frame data has {len(self.rgb)} bytes, expected {self.width * self.height * 3}")


class CaptureBackend(Protocol):
    name: str

    def grab(self, window: WindowInfo) -> Frame: ...


class MssBackend:
    """GDI screen copy of the window's client rectangle (fast, tiny; sees whatever is on top of the game)."""

    name = "mss"

    def grab(self, window: WindowInfo) -> Frame:
        import mss

        area = window.client
        try:
            with mss.MSS() as screen:
                shot = screen.grab({"left": area.left, "top": area.top, "width": area.width, "height": area.height})
        except mss.exception.ScreenShotError as exc:
            raise CaptureError(f"screen capture failed: {exc}") from exc
        return Frame(width=shot.width, height=shot.height, rgb=bytes(shot.rgb), backend=self.name, window=window)


def frame_warnings(frame: Frame, *, samples: int = 4096, seed: int = 0) -> list[str]:
    """Problems visible in the picture itself (never silently accepted)."""
    warnings = []
    pixels = frame.width * frame.height
    rng = random.Random(seed)
    picks = [rng.randrange(pixels) for _ in range(min(samples, pixels))]
    luma = [(frame.rgb[3 * i] * 299 + frame.rgb[3 * i + 1] * 587 + frame.rgb[3 * i + 2] * 114) / 1000 for i in picks]
    if len(luma) > 1 and statistics.pstdev(luma) < FLAT_FRAME_STDEV:
        warnings.append(
            "the picture is blank (one flat colour): the game may be covered, minimised or blocking this capture "
            "method - bring the game to the front and try again"
        )
    expected = frame.window.client
    if (frame.width, frame.height) != (expected.width, expected.height):
        warnings.append(
            f"captured {frame.width}x{frame.height} but the window is {expected.width}x{expected.height} "
            "(display scaling?)"
        )
    return warnings


def save_png(frame: Frame, path: Path) -> Path:
    import mss.tools

    path.parent.mkdir(parents=True, exist_ok=True)
    mss.tools.to_png(frame.rgb, (frame.width, frame.height), level=6, output=str(path))
    return path


def capture_name(when: datetime, label: str | None, existing: Callable[[str], bool]) -> str:
    """'20261004-103000-hero_info.png'; a counter avoids overwriting captures taken in the same second."""
    safe = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "_" for c in (label or "screen"))[:40]
    stem = f"{when.strftime('%Y%m%d-%H%M%S')}-{safe or 'screen'}"
    name, counter = f"{stem}.png", 1
    while existing(name):
        counter += 1
        name = f"{stem}-{counter}.png"
    return name


def describe(window: WindowInfo) -> str:
    rect: Rect = window.client
    return f"{window.title or '(no title)'} [{window.class_name}, {window.exe or '?'}] {rect.width}x{rect.height}"
