"""The program's icon (SPEC D85): a round hero portrait with the game's gold ring, for the window and the taskbar.

Celine's face (`c1103`, from the hero icons the app keeps in its own cache) is the default. A picture of the user's
own, e.g. a skin's face, wins: it is saved as `icon.png` in the data folder (the Settings page does it), so no game
picture is ever part of the repository. Without either, a plain emblem is drawn.
"""

from __future__ import annotations

import contextlib
import logging
import struct
import sys
from pathlib import Path
from typing import Final

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from e7ac import crashlog
from e7ac.paths import AppPaths
from e7ac.ui.art import Portraits, round_image
from e7ac.ui.theme import TOKENS

ICON_HERO: Final = "c1103"
"""Celine, by the catalog's code."""
APP_ID: Final = "OrbisCodex.Arena"
"""Windows groups the taskbar button by this id: without one the window wears python's icon."""
OVERRIDE_NAME: Final = "icon.png"
"""In the data folder: the user's own picture."""
SIZES: Final = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
ICO_NAME: Final = "orbis.ico"
ICO_SIZES: Final = (16, 24, 32, 48, 64, 128, 256)
MAX_SIDE: Final = 512


def override_path(paths: AppPaths) -> Path:
    return paths.home / OVERRIDE_NAME


def picture(paths: AppPaths) -> QImage | None:
    """The picture the icon is made of: the user's own, else Celine's cached portrait; None when there is neither."""
    own = _override(paths)
    if own is not None:
        return own
    data = Portraits(paths.cache_dir).icon_bytes(ICON_HERO)
    image = QImage.fromData(data) if data else QImage()
    return None if image.isNull() else image


def _round(image: QImage | None, side: int) -> QPixmap:
    """The icon at exactly `side` x `side` pixels (icons are in device pixels: no screen scale in them)."""
    return round_image(image, side, 1.0) if image is not None else _emblem(side)


def app_icon(paths: AppPaths) -> QIcon:
    """The user's picture, else Celine's cached portrait, else the emblem; always a usable icon."""
    image = picture(paths)
    icon = QIcon()
    for side in SIZES:
        icon.addPixmap(_round(image, side))
    return icon


def ico_path(paths: AppPaths) -> Path:
    return paths.home / ICO_NAME


def ico_bytes(paths: AppPaths) -> bytes:
    """The icon as a Windows `.ico` file (PNG pictures inside, from Windows Vista on): for shortcuts, whose icon the
    shell reads from a file."""
    image = picture(paths)
    pictures = []
    for side in ICO_SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        _round(image, side).save(buffer, "PNG")
        pictures.append((side, buffer.data().data()))
    header = struct.pack("<HHH", 0, 1, len(pictures))
    offset = len(header) + 16 * len(pictures)
    entries, blobs = b"", b""
    for side, png in pictures:
        entries += struct.pack("<BBBBHHII", side % 256, side % 256, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    return header + entries + blobs


def write_ico(paths: AppPaths) -> Path | None:
    """`orbis.ico` in the data folder, made again only when it would change. None when it cannot be written."""
    target = ico_path(paths)
    try:
        data = ico_bytes(paths)
        if not target.is_file() or target.read_bytes() != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    except OSError:
        return None
    return target


def source(paths: AppPaths) -> str:
    """Where the icon comes from now, in words."""
    if _override(paths) is not None:
        return "your own picture"
    return "Celine's portrait" if Portraits(paths.cache_dir).has(ICON_HERO) else "the plain emblem (no portraits yet)"


def install(app: QApplication, paths: AppPaths) -> None:
    """Icon for every window of the program, and its own taskbar button on Windows."""
    if sys.platform == "win32":
        import ctypes

        with contextlib.suppress(AttributeError, OSError):  # the icon still shows on the window itself
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)  # type: ignore[attr-defined,unused-ignore]
    icon = app_icon(paths)
    app.setWindowIcon(icon)
    shown = ", ".join(str(s.width()) for s in icon.availableSizes())
    crashlog.note(f"icon: {source(paths)}; sizes {shown}; windows icon file {write_ico(paths)}", logging.INFO)


def use_picture(paths: AppPaths, picture: Path) -> str:
    """Make `picture` the icon: cropped to a square, shrunk, saved as `icon.png` in the data folder. "" when done, else
    why not (the previous icon is left alone)."""
    image = QImage(str(picture))
    if image.isNull():
        return f"{picture.name} is not a picture that can be read"
    target = override_path(paths)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not _square(image).save(str(target)):  # the .png suffix picks the format
            return f"{target} could not be written"
    except OSError as exc:
        return str(exc)
    return ""


def use_default(paths: AppPaths) -> str:
    """Back to Celine's portrait: delete the user's picture. "" when done, else why not."""
    try:
        override_path(paths).unlink(missing_ok=True)
    except OSError as exc:
        return str(exc)
    return ""


def _override(paths: AppPaths) -> QImage | None:
    path = override_path(paths)
    if not path.is_file():
        return None
    image = QImage(str(path))
    return None if image.isNull() else _square(image)


def _square(image: QImage) -> QImage:
    """The middle square of a picture, at most `MAX_SIDE` pixels a side (a face is in the middle, not at the edges)."""
    side = min(image.width(), image.height())
    cut = image.copy((image.width() - side) // 2, (image.height() - side) // 2, side, side)
    if side > MAX_SIDE:
        cut = cut.scaled(
            MAX_SIDE, MAX_SIDE, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
    return cut


def _emblem(size: int) -> QPixmap:
    """No portrait: a dark disc, a gold ring and an "O"."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    ring = max(2.0, size / 14)
    painter.setBrush(QColor(TOKENS["side"]))
    painter.setPen(QPen(QColor(TOKENS["gold"]), ring))
    painter.drawEllipse(QRectF(ring / 2, ring / 2, size - ring, size - ring))
    font = QFont("Georgia")
    font.setPixelSize(max(6, round(size * 0.52)))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor(TOKENS["gold_bright"]))
    painter.drawText(QRectF(0, 0, size, size).translated(QPointF(0, size * 0.02)), Qt.AlignmentFlag.AlignCenter, "O")
    painter.end()
    return pixmap
