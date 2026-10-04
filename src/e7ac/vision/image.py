"""Load captures from disk (PNG/WebP/JPEG), including paths with non-ASCII characters on Windows."""

from __future__ import annotations

import glob
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

_CAPTURE_STAMP: Final = re.compile(r"^(\d{8}-\d{6})")
IMAGE_SUFFIXES: Final = frozenset({".png", ".webp", ".jpg", ".jpeg", ".bmp"})
_GLOB_CHARS: Final = frozenset("*?[")


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


def expand_image_paths(arguments: Sequence[str | Path]) -> list[Path]:
    """The image files named by command-line arguments, in capture order (SPEC D43).

    Windows shells pass `*.png` and `%LOCALAPPDATA%` through unexpanded, so this does it: environment variables and
    `~` are expanded; an existing file or folder is taken literally (a folder gives its images); otherwise a pattern
    with `*`, `?` or `[` is matched. A folder or pattern that gives no image is an error, never silently skipped."""
    found: list[Path] = []
    for argument in arguments:
        path = Path(os.path.expandvars(str(argument))).expanduser()
        text = str(path)
        if path.is_dir():
            images = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
            if not images:
                raise ImageError(f"no images (PNG, WebP, JPEG) in the folder {path}")
            found.extend(_in_capture_order(images))
        elif not path.exists() and _GLOB_CHARS & set(text):
            # glob.glob, not Path.glob: the pattern may be absolute with wildcards in any part ("C:\\x\\*\\*.png")
            matches = glob.glob(text)  # noqa: PTH207
            images = [Path(m) for m in matches if Path(m).is_file() and Path(m).suffix.lower() in IMAGE_SUFFIXES]
            if not images:
                raise ImageError(f"no image matches {text}")
            found.extend(_in_capture_order(images))
        else:
            found.append(path)  # a missing file is reported when it is loaded
    unique: dict[Path, None] = dict.fromkeys(found)
    return list(unique)


def _in_capture_order(paths: list[Path]) -> list[Path]:
    """Oldest first, so the newest capture of a hero becomes its current build."""
    return sorted(paths, key=lambda p: (captured_at(p), p.name))
