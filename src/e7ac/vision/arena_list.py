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
from e7ac.vision.arena_badges import ArenaScreenError, BgrImage, Template, class_in_region, level_squares
from e7ac.vision.arena_guide import list_rows

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


# --- finding the rows from the level squares (SPEC D84) ---------------------------------------------------------
FACE_PITCH: Final = 2.18
"""Distance between two neighbouring faces of a row, in face radii (measured: 94 px at a radius of 43 px)."""
SQUARE_DX: Final = 0.82
SQUARE_DY: Final = -0.57
"""Where the level square's centre lies from the face centre, in radii (12 faces: +0.80..+0.93, -0.55..-0.66)."""
SQUARE_H: Final = 0.52
"""A level square's height in face radii (0.48-0.56)."""
COLUMNS: Final = 4

type Face = tuple[float, float, float]


def find_rows(image: BgrImage) -> list[list[Face]]:
    """The faces of the Opponents list, row by row, top to bottom (SPEC D84). Circle detection (`list_rows`) first, as
    its centres are the most exact; it is sensitive to the picture's size and to blur and may miss a row or a face, so
    the rows it did not find are taken from the level squares, whose colour is found whatever the size (the squares of
    a row are evenly spaced, four of them, one over each face). Raises ArenaScreenError unless there are two rows."""
    try:
        found = list_rows(image)
    except ArenaScreenError:
        found = []
    rows = list(found)
    for row in rows_from_squares(image):
        if all(abs(row[0][1] - known[0][1]) > 0.6 * row[0][2] for known in rows):
            rows.append(row)  # a row the circles missed
    if len(rows) < 2:
        raise ArenaScreenError("no rows of four hero icons: not the Opponents list")
    return sorted(rows, key=lambda row: row[0][1])


def rows_from_squares(image: BgrImage) -> list[list[Face]]:
    """Rows of four faces placed from the level squares: squares of one size in rows (same height, three or four of
    them) whose columns line up from row to row. Empty when there is no such grid."""
    squares = [(x + w / 2, y + h / 2, h) for x, y, w, h, _, _ in level_squares(image)]
    if len(squares) < 6:
        return []
    median = float(np.median([s[2] for s in squares]))
    squares = sorted((s for s in squares if 0.8 * median <= s[2] <= 1.25 * median), key=lambda s: s[1])
    groups: list[list[tuple[float, float, float]]] = []
    for square in squares:
        if groups and abs(square[1] - float(np.mean([s[1] for s in groups[-1]]))) <= 0.6 * median:
            groups[-1].append(square)
        else:
            groups.append([square])
    groups = [sorted(g, key=lambda s: s[0]) for g in groups if len(g) >= 3]
    if len(groups) < 2:
        return []
    radius = median / SQUARE_H
    pitch = FACE_PITCH * radius
    columns = _columns([s[0] for g in groups for s in g], pitch)
    if len(columns) != COLUMNS:
        return []
    rows = []
    for group in groups:
        if any(min(abs(s[0] - c) for c in columns) > 0.35 * pitch for s in group):
            return []  # a square between the columns: not a grid of faces
        y = float(np.mean([s[1] for s in group])) - SQUARE_DY * radius
        rows.append([(c - SQUARE_DX * radius, y, radius) for c in columns])
    return rows


def _columns(xs: list[float], pitch: float) -> list[float]:
    """The x positions the squares pile up at (a square within a third of a pitch of one belongs to it)."""
    centres: list[list[float]] = []
    for x in sorted(xs):
        if centres and x - float(np.mean(centres[-1])) <= pitch / 3:
            centres[-1].append(x)
        else:
            centres.append([x])
    return [float(np.mean(c)) for c in centres if len(c) >= 2]
