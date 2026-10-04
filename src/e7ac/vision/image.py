"""Load captures from disk (PNG/WebP/JPEG), including paths with non-ASCII characters on Windows."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

_CAPTURE_STAMP: Final = re.compile(r"^(\d{8}-\d{6})")


class ImageError(Exception):
    """The file cannot be read as an image."""


def load_image(path: Path) -> Any:
    """BGR image array. `cv2.imread` cannot open non-ASCII paths on Windows, so the bytes are decoded instead."""
    import cv2
    import numpy as np

    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError as exc:
        raise ImageError(f"cannot read {path}: {exc.strerror or exc}") from exc
    image = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if image is None:
        raise ImageError(f"{path} is not an image (PNG, WebP or JPEG expected)")
    return image


def captured_at(path: Path) -> datetime:
    """When a capture was taken: from `e7 capture` file names (UTC stamp), else the file's modification time."""
    match = _CAPTURE_STAMP.match(path.name)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
