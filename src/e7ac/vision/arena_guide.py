"""Arena Attack Guide (the magnifier on the VS screen): the opponent's defence team as round face icons (M17a,
SPEC D66).

The faces are the game's own hero icons, the ones Stove publishes as `{code}_s.png` (`sources.assets`), so they are
matched directly against every hero of the catalog: no learning, no question. Measured on the user's capture
(2026-10-12, 391 heroes): the icon spans the frame's inner circle (scale 1.0), true faces scored 0.91-0.96 with the
runner-up at least 0.32 lower; a face missing from the catalog (a newer hero or a skin) scored 0.56 with no margin.
A face is accepted only with `MIN_SCORE` and `MIN_MARGIN` (`assumed` from that capture): never a near miss.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import cv2
import numpy as np
import numpy.typing as npt

from e7ac.vision.arena_badges import ArenaScreenError, BgrImage

SIZE: Final = 64
"""Faces are compared at this size (inner disc of the frame)."""
INNER: Final = 0.86
"""Inner disc / frame radius (the gold rim left out)."""
SCALES: Final = (0.95, 1.0, 1.05)
MIN_SCORE: Final = 0.80
MIN_MARGIN: Final = 0.15
SHORTLIST: Final = 12
"""Heroes compared in full after a quick whole-face comparison of all of them (the true face ranked 1st there)."""
_BACKDROP: Final = np.array([110, 60, 20], np.float32)
"""Where a reference icon is transparent, the game shows a blue backdrop (BGR, approximate)."""


@dataclass(frozen=True, slots=True)
class Face:
    x: float
    y: float
    r: float
    code: str | None
    """None when no hero is clearly the best (`candidate` is then only the closest)."""
    score: float
    margin: float
    candidate: str


class FaceMatcher:
    def __init__(self, references: Mapping[str, list[npt.NDArray[np.float32]]]) -> None:
        self._references = references
        mask = np.zeros((SIZE, SIZE), np.float32)
        cv2.circle(mask, (SIZE // 2, SIZE // 2), SIZE // 2 - 1, 1.0, -1)
        corner = round(0.38 * SIZE)
        mask[:corner, :corner] = 0  # the class icon overlaps this corner of the frame
        self._mask = np.dstack([mask] * 3)
        self._keep = self._mask.astype(bool)
        self._codes = list(references)
        centre = SCALES.index(1.0)
        self._quick = (
            np.stack([_vector(_centre(references[c][centre]), self._keep) for c in self._codes])
            if self._codes
            else np.zeros((0, 1), np.float32)
        )

    @classmethod
    def from_png(cls, icons: Mapping[str, bytes]) -> FaceMatcher:
        references: dict[str, list[npt.NDArray[np.float32]]] = {}
        for code, data in icons.items():
            icon = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
            if icon is None or icon.ndim != 3 or icon.shape[2] != 4:
                continue  # not an RGBA icon: left out (the caller reports missing icons)
            rgba = np.asarray(icon, np.uint8)
            references[code] = [_composite(rgba, round(SIZE / INNER * k)) for k in SCALES]
        return cls(references)

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(self._references)

    def read(self, image: BgrImage) -> list[Face]:
        """The faces of the defence team, left to right. Raises ArenaScreenError when there is no row of icons."""
        faces = []
        for x, y, r in find_row(image):
            side = round(2 * r * INNER)
            x0, y0 = round(x - side / 2), round(y - side / 2)
            if x0 < 0 or y0 < 0 or x0 + side > image.shape[1] or y0 + side > image.shape[0]:
                continue
            crop = cv2.resize(image[y0 : y0 + side, x0 : x0 + side], (SIZE, SIZE), interpolation=cv2.INTER_AREA)
            faces.append(self._identify(np.asarray(crop, np.float32), x, y, r))
        return faces

    def _identify(self, crop: npt.NDArray[np.float32], x: float, y: float, r: float) -> Face:
        quick = self._quick @ _vector(crop, self._keep) if self._codes else np.zeros(0)
        shortlist = [self._codes[i] for i in np.argsort(-quick)[:SHORTLIST]]
        scores = {}
        for code in shortlist:
            variants = self._references[code]
            best = -1.0
            for reference in variants:
                result = cv2.matchTemplate(reference, crop, cv2.TM_CCOEFF_NORMED, mask=self._mask)
                result[~np.isfinite(result)] = -1
                best = max(best, float(result.max()))
            scores[code] = best
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        if not ranked:
            return Face(x, y, r, None, 0.0, 0.0, "")
        top, score = ranked[0]
        margin = score - (ranked[1][1] if len(ranked) > 1 else 0.0)
        accepted = score >= MIN_SCORE and margin >= MIN_MARGIN
        return Face(x, y, r, top if accepted else None, round(score, 3), round(margin, 3), top)


def find_row(image: BgrImage) -> list[tuple[float, float, float]]:
    """Round icons of one size side by side (2-4 of them), left to right."""
    height = image.shape[0]
    grey = cv2.medianBlur(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 5)
    found = cv2.HoughCircles(
        grey,
        cv2.HOUGH_GRADIENT,
        dp=1,
        minDist=height * 0.1,
        param1=120,
        param2=30,
        minRadius=int(height * 0.05),
        maxRadius=int(height * 0.1),
    )
    if found is None:
        raise ArenaScreenError("no round icons: not the Attack Guide")
    circles = [(float(c[0]), float(c[1]), float(c[2])) for c in np.asarray(found)[0]]
    rows = [
        [c for c in circles if abs(c[1] - anchor[1]) <= 0.2 * anchor[2] and abs(c[2] - anchor[2]) <= 0.15 * anchor[2]]
        for anchor in circles
    ]
    row = max(rows, key=len)
    if not 2 <= len(row) <= 4:
        raise ArenaScreenError("no row of hero icons: not the Attack Guide")
    return sorted(row)


def _centre(reference: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    top = (reference.shape[0] - SIZE) // 2
    return reference[top : top + SIZE, top : top + SIZE]


def _vector(face: npt.NDArray[np.float32], keep: npt.NDArray[np.bool_]) -> npt.NDArray[np.float32]:
    """Masked pixels, zero mean, unit length: a dot product of two is their correlation."""
    values = face[keep].astype(np.float32)
    values -= values.mean()
    norm = float(np.linalg.norm(values))
    return np.asarray(values / norm if norm else values, np.float32)


def _composite(icon: npt.NDArray[np.uint8], size: int) -> npt.NDArray[np.float32]:
    """The icon at `size` over the blue backdrop, padded so small shifts are searched too."""
    resized = cv2.resize(icon, (size, size), interpolation=cv2.INTER_AREA)
    pad = max(0, (SIZE - size) // 2) + 4
    canvas = np.zeros((size + 2 * pad, size + 2 * pad, 4), np.uint8)
    canvas[pad : pad + size, pad : pad + size] = resized
    alpha = canvas[:, :, 3:4].astype(np.float32) / 255
    return np.asarray(canvas[:, :, :3].astype(np.float32) * alpha + _BACKDROP * (1 - alpha), np.float32)
