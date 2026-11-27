"""The skill's own icon, cut from the picture it was read from (SPEC D134).

A skill popup shows the skill's icon at the left of its title. When a popup is read, the icon is cut out and kept in
`<home>/skills/icons/<hero code>-<skill key>.png` (local, never committed: they are the game's art), for the Hero page.
The place is worked out from the title's box, so it follows the size of the game window. Pure image maths."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import cv2
import numpy as np
import numpy.typing as npt

from e7ac.catalog.names import normalise_name

type BgrImage = npt.NDArray[np.uint8]

ART_DIR: Final = Path(__file__).resolve().parents[3] / "assets" / "art" / "skills"
"""The icons kept in the repository (SPEC D135, temporarily public: see the release checklist in CLAUDE.md); a copy in
`<home>/skills/icons/` wins over them."""
FOLDER: Final = "icons"
SIDE: Final = 2.6
"""The icon's side, in heights of the title's text (the game's popups: a 38 px title, a 98 px icon)."""
GAP: Final = 0.45
"""Between the icon and the title, in heights of the title."""
RAISE: Final = 0.3
"""The icon starts this much above the title's top, in heights of the title."""
MIN_SIDE: Final = 24


def icons_dir(home: Path) -> Path:
    return home / "skills" / FOLDER


def icon_path(home: Path, code: str, skill_name: str) -> Path:
    return icons_dir(home) / f"{code}-{normalise_name(skill_name)}.png"


def find_icon(home: Path, code: str, skill_name: str) -> Path | None:
    """The icon file of a skill: the one cut on this PC, else the one that comes with the program."""
    name = icon_path(home, code, skill_name).name
    for folder in (icons_dir(home), ART_DIR):
        if (folder / name).is_file():
            return folder / name
    return None


def crop_icon(image: BgrImage, title_box: tuple[float, float, float, float]) -> BgrImage | None:
    """The icon left of the title, or None when it would fall outside the picture or be tiny."""
    x0, y0, _, y1 = title_box
    height = y1 - y0
    side = round(SIDE * height)
    left, top = round(x0 - GAP * height - side), round(y0 - RAISE * height)
    rows, cols = image.shape[:2]
    if side < MIN_SIDE or left < 0 or top < 0 or left + side > cols or top + side > rows:
        return None
    return np.asarray(image[top : top + side, left : left + side], np.uint8).copy()


def save_icon(
    home: Path, code: str, skill_name: str, image: BgrImage, title_box: tuple[float, float, float, float]
) -> Path | None:
    """Cut and keep the icon (the latest reading wins); the path, or None when there was nothing to cut."""
    icon = crop_icon(image, title_box)
    if icon is None:
        return None
    path = icon_path(home, code, skill_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, data = cv2.imencode(".png", icon)
    if not ok:
        return None
    data.tofile(str(path))
    return path
