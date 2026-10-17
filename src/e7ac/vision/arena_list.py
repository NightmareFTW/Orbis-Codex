"""The badges the Opponents list shows over each face (SPEC D82): the class icon at the top left of the round face and
the level square (coloured by element) at its top right. Same icons and colours as the VS screen (`arena_badges`),
other places: they are found from the face circle, in face radii, so any capture size works.

What this gives: the element and class of every hero of every defence team, even of a face that is not recognised, and
a check on the face match (a hero whose element or class disagrees with its badge is not the hero shown).
Measured on the user's full-screen capture of 2026-10-18 (12 faces, radius 43 px at 740 px high): the icon centre lies
0.8 radii left of and 0.55 above the face centre, the square 0.77 right and 0.55 above, 0.55 radii tall.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import cv2
import numpy as np

from e7ac.domain.codes import Element, HeroClass
from e7ac.vision.arena_badges import BgrImage, Template, class_in_region, level_squares

SQUARE_X: Final = (0.35, 1.30)
SQUARE_Y: Final = (-1.30, -0.05)
"""Where the level square's centre may lie, in face radii from the face centre (x right, y down)."""
ICON_X: Final = (-1.40, -0.15)
ICON_Y: Final = (-1.30, -0.05)
SQUARE_SIZE: Final = (0.35, 0.80)
"""Height of a level square in face radii."""


@dataclass(frozen=True, slots=True)
class ListBadge:
    element: Element | None
    """None when no level square was found over the face."""
    element_confidence: float
    hero_class: HeroClass | None
    """None when the class icon was not clearly one of the six."""
    class_confidence: float

    @property
    def shape(self) -> tuple[str, str] | None:
        """(element, class) as the catalog writes them, only when both were read."""
        if self.element is None or self.hero_class is None:
            return None
        return self.element.value, self.hero_class.value


def read_list_badges(
    image: BgrImage,
    rows: Sequence[Sequence[tuple[float, float, float]]],
    templates: Mapping[HeroClass, Template],
) -> list[list[ListBadge]]:
    """One badge per face of every row (the rows as `arena_guide.list_rows` gives them)."""
    squares = level_squares(image)
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    height, width = grey.shape
    out: list[list[ListBadge]] = []
    for row in rows:
        badges = []
        for x, y, r in row:
            found = [
                s
                for s in squares
                if SQUARE_SIZE[0] <= s[3] / r <= SQUARE_SIZE[1]
                and SQUARE_X[0] <= (s[0] + s[2] / 2 - x) / r <= SQUARE_X[1]
                and SQUARE_Y[0] <= (s[1] + s[3] / 2 - y) / r <= SQUARE_Y[1]
            ]
            element, element_confidence, side = None, 0.0, max(3, round(0.55 * r))
            if found:
                best = max(found, key=lambda s: s[5])
                element, element_confidence, side = best[4], best[5], best[3]
            region = (
                max(0, round(x + ICON_X[0] * r)),
                max(0, round(y + SQUARE_Y[0] * r)),
                min(width, round(x + ICON_X[1] * r)),
                min(height, round(y + ICON_Y[1] * r)),
            )
            read = (
                class_in_region(grey, region, side, templates)
                if region[2] > region[0] and region[3] > region[1]
                else None
            )
            badges.append(ListBadge(element, element_confidence, read[0] if read else None, read[1] if read else 0.0))
        out.append(badges)
    return out
