"""The game's heroes as decoration (SPEC D82): round portraits from the face icons the app already keeps in its own
cache (downloaded once from Stove by the Arena watch or the Home page). They are never copied into the repository and
never fetched here: a portrait that is not cached yet is a quiet placeholder, not a request.
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient

from e7ac.sources.assets import load_hero_icons
from e7ac.ui.theme import TOKENS


class Portraits:
    """Round hero portraits with a gold ring, by hero code and size (cached)."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._bytes: dict[str, bytes | None] = {}
        self._made: dict[tuple[str, int], QPixmap] = {}

    def reset(self) -> None:
        """Forget what was looked up (after the icons were downloaded)."""
        self._bytes.clear()
        self._made.clear()

    def has(self, code: str) -> bool:
        return self._icon(code) is not None

    def cached(self, codes: Sequence[str]) -> list[str]:
        """The codes among `codes` whose portrait is in the cache, in order."""
        return [c for c in codes if self.has(c)]

    def pixmap(self, code: str | None, size: int) -> QPixmap:
        """The portrait, or an empty ring when the hero is unknown or not cached."""
        key = (code or "", size)
        if key not in self._made:
            data = self._icon(code) if code else None
            self._made[key] = _round(data, size)
        return self._made[key]

    def _icon(self, code: str | None) -> bytes | None:
        if not code:
            return None
        if code not in self._bytes:
            self._bytes[code] = load_hero_icons(self._cache_dir, [code]).get(code)
        return self._bytes[code]


def _ratio() -> float:
    screen = QGuiApplication.primaryScreen()
    return max(1.0, float(screen.devicePixelRatio())) if screen is not None else 1.0


def _round(data: bytes | None, size: int) -> QPixmap:
    return round_image(QImage.fromData(data) if data else QImage(), size)


def round_from_bgr(picture: npt.NDArray[np.uint8], size: int) -> QPixmap:
    """A round portrait of a picture the watch cut from the screen (BGR, as OpenCV has it)."""
    height, width = picture.shape[:2]
    image = QImage(picture.data, width, height, 3 * width, QImage.Format.Format_BGR888).copy()
    return round_image(image, size)


def round_image(image: QImage, size: int) -> QPixmap:
    ratio = _ratio()
    side = round(size * ratio)
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    ring = max(2.0, size / 24)
    inner = QRectF(ring, ring, size - 2 * ring, size - 2 * ring)
    disc = QPainterPath()
    disc.addEllipse(inner)
    if image.isNull():  # not cached: a dark disc with a faint glow
        glow = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
        glow.setColorAt(0.0, QColor(TOKENS["panel_alt"]))
        glow.setColorAt(1.0, QColor(TOKENS["side"]))
        painter.fillPath(disc, glow)
    else:
        painter.setClipPath(disc)
        painter.drawImage(inner, image)
        painter.setClipping(False)
    painter.setPen(QPen(QColor(TOKENS["gold_dim"]), ring))  # the game's portraits: a bronze rim with a gold edge
    painter.drawEllipse(QRectF(ring / 2, ring / 2, size - ring, size - ring))
    painter.setPen(QPen(QColor(TOKENS["gold_bright"]), max(1.0, ring / 3)))
    painter.drawEllipse(QRectF(ring, ring, size - 2 * ring, size - 2 * ring))
    painter.end()
    return pixmap


def hero_of_the_day(codes: Sequence[str], today: datetime.date | None = None) -> str | None:
    """One of `codes`, the same all day and another tomorrow (the sidebar's featured hero)."""
    if not codes:
        return None
    return codes[((today or datetime.date.today()).toordinal()) % len(codes)]
