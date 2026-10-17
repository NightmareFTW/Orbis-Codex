"""Arena Attack Guide (the magnifier on the VS screen): the opponent's defence team as round face icons (M17a,
SPEC D66).

The faces are the game's own hero icons, the ones Stove publishes as `{code}_s.png` (`sources.assets`), so they are
matched directly against every hero of the catalog: no learning, no question. Measured on the user's capture
(2026-10-12, 391 heroes): the icon spans the frame's inner circle (scale 1.0), true faces scored 0.91-0.96 with the
runner-up at least 0.32 lower; a face missing from the catalog (a newer hero or a skin) scored 0.56 with no margin.
A face is accepted only with `MIN_SCORE` and `MIN_MARGIN` (`assumed` from that capture): never a near miss.
Some in-game faces differ from the Stove icon (Sea Phantom Politis, 2026-10-13: 0.48): faces named once (by the user
or by elimination against the VS screen) are learned and matched like the icons (SPEC D69).
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping, Sequence
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
GROUP_MIN_SCORE: Final = 0.60
GROUP_MIN_MARGIN: Final = 0.10
"""A face not recognised among all heroes may still be clear among the few the VS screen allows (its badge's element
and class, SPEC D70): accepted there with these (`assumed`; Briar Witch Iseria scored 0.73 against its own icon)."""
LIST_INNER: Final = 0.80
LIST_CORNER: Final = 0.35
LIST_BOTTOM: Final = 0.70
"""The Opponents list frames the same icons smaller, with a class icon (top left), a level badge (top right) and
stars (bottom) laid over them: those parts are left out (12/12 faces of the user's capture accepted, 2026-10-16)."""
SHORTLIST: Final = 40
"""Heroes compared in full after a quick whole-face comparison of all of them (12 missed Rinak on the user's
capture, 2026-10-13; 40: found, ~0.5 s per Attack Guide)."""
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
    crop: npt.NDArray[np.uint8] | None = None
    """The face as compared (SIZE x SIZE): kept so a face named later can be learned."""


class FaceMatcher:
    def __init__(self, references: Mapping[str, list[npt.NDArray[np.float32]]], layout: str = "guide") -> None:
        mask = np.zeros((SIZE, SIZE), np.float32)
        cv2.circle(mask, (SIZE // 2, SIZE // 2), SIZE // 2 - 1, 1.0, -1)
        corner = round((0.38 if layout == "guide" else LIST_CORNER) * SIZE)
        mask[:corner, :corner] = 0  # the class icon overlaps this corner of the frame
        if layout == "list":  # the Opponents list also lays the level badge (top right) and the stars (bottom) on it
            mask[:corner, SIZE - corner :] = 0
            mask[round(LIST_BOTTOM * SIZE) :, :] = 0
        self.inner = INNER if layout == "guide" else LIST_INNER
        self._mask = np.dstack([mask] * 3)
        self._keep = self._mask.astype(bool)
        self._entries: list[tuple[str, list[npt.NDArray[np.float32]]]] = []
        self._quick: list[npt.NDArray[np.float32]] = []
        self._twins: list[FaceMatcher] = []
        for code, variants in references.items():
            self._add(code, variants)

    def for_list(self) -> FaceMatcher:
        """The same heroes for the faces of the Opponents list (another framing; references shared, not copied)."""
        twin = FaceMatcher({}, layout="list")
        for code, variants in self._entries:
            twin._add(code, variants)
        self._twins.append(twin)  # a face learned here is known there too
        return twin

    def read_list(self, image: BgrImage) -> list[list[Face]]:
        """Each opponent's defence team on the Opponents list, top to bottom (rows of four faces)."""
        return self.read_rows(image, list_rows(image))

    def read_rows(self, image: BgrImage, rows: Sequence[Sequence[tuple[float, float, float]]]) -> list[list[Face]]:
        """The faces of rows already found (`list_rows`)."""
        return [[self._face(image, x, y, r) for x, y, r in row] for row in rows]

    @classmethod
    def from_png(
        cls, icons: Mapping[str, bytes], learned: Mapping[str, list[npt.NDArray[np.uint8]]] | None = None
    ) -> FaceMatcher:
        """`learned`: faces named before (SIZE x SIZE crops), per hero code."""
        references: dict[str, list[npt.NDArray[np.float32]]] = {}
        for code, data in icons.items():
            icon = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
            if icon is None or icon.ndim != 3 or icon.shape[2] != 4:
                continue  # not an RGBA icon: left out (the caller reports missing icons)
            rgba = np.asarray(icon, np.uint8)
            references[code] = [_composite(rgba, round(SIZE / INNER * k)) for k in SCALES]
        matcher = cls(references)
        for code, crops in (learned or {}).items():
            for crop in crops:
                matcher.learn(code, crop)
        return matcher

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(code for code, _ in self._entries)

    def learn(self, code: str, crop: npt.NDArray[np.uint8]) -> None:
        """A face named once: matched like an icon from now on (small shifts searched)."""
        face = cv2.resize(crop, (SIZE, SIZE), interpolation=cv2.INTER_AREA).astype(np.float32)
        padded = np.asarray(cv2.copyMakeBorder(face, 4, 4, 4, 4, cv2.BORDER_REPLICATE), np.float32)
        self._add(code, [padded] * len(SCALES))
        for twin in self._twins:
            twin._add(code, [padded] * len(SCALES))

    def read(self, image: BgrImage) -> list[Face]:
        """The faces of the defence team, left to right. Raises ArenaScreenError when there is no row of icons."""
        faces = []
        for x, y, r in find_row(image):
            side = round(2 * r * self.inner)
            x0, y0 = round(x - side / 2), round(y - side / 2)
            if x0 < 0 or y0 < 0 or x0 + side > image.shape[1] or y0 + side > image.shape[0]:
                continue
            faces.append(self._face(image, x, y, r))
        return faces

    def _face(self, image: BgrImage, x: float, y: float, r: float) -> Face:
        side = round(2 * r * self.inner)
        x0, y0 = max(0, round(x - side / 2)), max(0, round(y - side / 2))
        crop = cv2.resize(image[y0 : y0 + side, x0 : x0 + side], (SIZE, SIZE), interpolation=cv2.INTER_AREA)
        return self._identify(np.asarray(crop, np.uint8), x, y, r)

    def among(self, face: Face, codes: Iterable[str]) -> tuple[str | None, float, float]:
        """The best of `codes` for this face: (its code when clear by the group rule, score, margin)."""
        ranked = self._ranked(face, set(codes))
        if not ranked:
            return None, 0.0, 0.0
        top, score = ranked[0]
        margin = score - (ranked[1][1] if len(ranked) > 1 else 0.0)
        clear = score >= GROUP_MIN_SCORE and margin >= GROUP_MIN_MARGIN
        return (top if clear else None), round(score, 3), round(margin, 3)

    def suggest(self, face: Face, codes: Iterable[str] | None = None, count: int = 6) -> list[tuple[str, float]]:
        """The heroes this face looks most like, best first, for the user to pick from (SPEC D83): among `codes`, or
        among the whole catalog when none are given (the quick comparison first, as in `_identify`)."""
        return [
            (code, round(score, 3)) for code, score in self._ranked(face, None if codes is None else set(codes))[:count]
        ]

    def _ranked(self, face: Face, wanted: set[str] | None) -> list[tuple[str, float]]:
        if face.crop is None or not self._entries:
            return []
        crop = face.crop.astype(np.float32)
        if wanted is None:
            quick = np.stack(self._quick) @ _vector(crop, self._keep)
            entries = [self._entries[int(i)] for i in np.argsort(-quick)[:SHORTLIST]]
        else:
            entries = [entry for entry in self._entries if entry[0] in wanted]
        scores: dict[str, float] = {}
        for code, variants in entries:
            for reference in variants:
                result = cv2.matchTemplate(reference, crop, cv2.TM_CCOEFF_NORMED, mask=self._mask)
                result[~np.isfinite(result)] = -1
                scores[code] = max(scores.get(code, -1.0), float(result.max()))
        return sorted(scores.items(), key=lambda kv: -kv[1])

    def _add(self, code: str, variants: list[npt.NDArray[np.float32]]) -> None:
        self._entries.append((code, variants))
        self._quick.append(_vector(_centre(variants[SCALES.index(1.0)]), self._keep))

    def _identify(self, face: npt.NDArray[np.uint8], x: float, y: float, r: float) -> Face:
        crop = face.astype(np.float32)
        if not self._entries:
            return Face(x, y, r, None, 0.0, 0.0, "", face)
        quick = np.stack(self._quick) @ _vector(crop, self._keep)
        scores: dict[str, float] = {}
        for index in np.argsort(-quick)[:SHORTLIST]:
            code, variants = self._entries[int(index)]
            for reference in variants:
                result = cv2.matchTemplate(reference, crop, cv2.TM_CCOEFF_NORMED, mask=self._mask)
                result[~np.isfinite(result)] = -1
                scores[code] = max(scores.get(code, -1.0), float(result.max()))
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        top, score = ranked[0]
        margin = score - (ranked[1][1] if len(ranked) > 1 else 0.0)
        accepted = score >= MIN_SCORE and margin >= MIN_MARGIN
        return Face(x, y, r, top if accepted else None, round(score, 3), round(margin, 3), top, face)


def list_rows(image: BgrImage) -> list[list[tuple[float, float, float]]]:
    """Rows of four round icons of one size, evenly spaced (the player's own round avatar on the left is not one of
    them): two or more such rows are the Opponents list. Raises ArenaScreenError otherwise."""
    height = image.shape[0]
    grey = cv2.medianBlur(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 5)
    found = cv2.HoughCircles(
        grey,
        cv2.HOUGH_GRADIENT,
        dp=1,
        minDist=height * 0.08,
        param1=120,
        param2=30,
        minRadius=int(height * 0.04),
        maxRadius=int(height * 0.08),
    )
    circles = sorted(
        ((float(c[0]), float(c[1]), float(c[2])) for c in np.asarray(found)[0]) if found is not None else []
    )
    rows: list[list[tuple[float, float, float]]] = []
    for anchor in circles:
        if any(anchor in row for row in rows):
            continue
        line = sorted(c for c in circles if abs(c[1] - anchor[1]) <= 0.3 * anchor[2])
        for start in range(len(line) - 3):
            four = line[start : start + 4]
            radii = [c[2] for c in four]
            gaps = [b[0] - a[0] for a, b in itertools.pairwise(four)]
            mean = sum(gaps) / 3
            if max(radii) <= 1.25 * min(radii) and all(abs(g - mean) <= 0.12 * mean for g in gaps):
                if four not in rows:
                    rows.append(four)
                break
    rows.sort(key=lambda row: row[0][1])
    if len(rows) < 2:
        raise ArenaScreenError("no rows of four hero icons: not the Opponents list")
    return rows


@dataclass(frozen=True, slots=True)
class ListLayout:
    """Where the Opponents list puts its faces on a screen of one size: the columns, the rows and the face radius."""

    height: int
    xs: tuple[float, ...]
    ys: tuple[float, ...]
    r: float


LIST_FACE_SPREAD: Final = 18.0
"""Smallest grey-level spread (standard deviation) of the square inside a face disc for it to count as a picture, not
the panel behind it. Measured on the user's two Opponents list captures (2026-10-18): the 24 faces 34.7-83.0, the
panel between two rows 8.6-10.9 on one of them and, where a row's glow reaches it, up to 65.6 on the other: a missing
row is only added when at least three of its four places hold a picture."""


def fit_rows(
    found: Sequence[Sequence[tuple[float, float, float]]], layout: ListLayout | None, image: BgrImage
) -> tuple[list[list[tuple[float, float, float]]], ListLayout]:
    """The rows of the Opponents list as its layout says (SPEC D83): circle detection jitters by a pixel or two from
    frame to frame and may miss a face or a whole row (a row fading in, a highlight on it). The faces found fix the
    layout (columns by the median of the rows, rows by position); every frame then reads the faces where the layout
    puts them, with a row the detection missed added when its four places hold pictures. A layout from another screen
    size, or whose columns do not fit these faces, is replaced."""
    height = image.shape[0]
    rows = [r for r in found if len(r) == 4]
    xs = tuple(float(np.median([row[i][0] for row in rows])) for i in range(4))
    radius = float(np.median([f[2] for row in rows for f in row]))
    ys = sorted(float(np.median([f[1] for f in row])) for row in rows)
    fits = (
        layout is not None
        and layout.height == height
        and all(abs(a - b) <= 0.3 * radius for a, b in zip(layout.xs, xs, strict=True))
        and abs(layout.r - radius) <= 0.2 * radius
    )
    if layout is None or not fits:
        layout = ListLayout(height, xs, tuple(ys), radius)
    else:
        merged = list(layout.ys)
        for y in ys:
            if all(abs(y - known) > 0.5 * layout.r for known in merged):
                merged.append(y)  # a row never seen before at this size
        layout = ListLayout(height, layout.xs, tuple(sorted(merged)), layout.r)
    grey = np.asarray(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), np.uint8)
    fitted: list[list[tuple[float, float, float]]] = []
    for y in layout.ys:
        faces = [(x, y, layout.r) for x in layout.xs]
        seen = any(abs(row[0][1] - y) <= 0.5 * layout.r for row in rows)
        if seen or sum(_has_picture(grey, f) for f in faces) >= 3:
            fitted.append(faces)
    return fitted, layout


def _has_picture(grey: npt.NDArray[np.uint8], face: tuple[float, float, float]) -> bool:
    x, y, r = face
    half = round(0.6 * r)
    patch = grey[max(0, round(y) - half) : round(y) + half, max(0, round(x) - half) : round(x) + half]
    return bool(patch.size and float(patch.std()) >= LIST_FACE_SPREAD)


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
