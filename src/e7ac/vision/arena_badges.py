"""Arena VS screen: the badge above each hero (white class icon + level square in the element's colour) and the crop
of the hero's model under it (M17a, SPEC D64).

The screen shows no names: a badge gives only element and class, which narrows the catalog to 6-23 heroes; the hero
itself is identified by `arena.identify`. Facts and measurements (prep_1 capture, 2026-10-12; study in SPEC D64):
- level square: hue bands measured for wind (green), light (yellow) and dark (purple) on the VS screen, and for fire
  (red) and ice (blue) on the Opponents list (2026-10-18: 12 of 12 squares agree with the heroes the faces named; the
  VS screen with a fire or ice hero is still to be seen, NV-34); a square is 2-9% of the image height, nearly
  square, holds 1-3 white digits;
- class icon: left of the square, 1.05-1.65 square heights tall; matched against the six class icons of the Stove
  guide sprite (`sources.assets`, verified for knight, mage, thief, soul weaver and, on the Opponents list, ranger;
  warrior not seen yet);
  a square without a recognised icon is not a badge (buttons with white text on other screens look like squares);
- side: left of the image centre = your team, right = the opponent's.
Deterministic OpenCV only; every length is in level-square heights, so any capture size works.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

import cv2
import numpy as np
import numpy.typing as npt

from e7ac.domain.codes import Element, HeroClass

type BgrImage = npt.NDArray[np.uint8]
type Template = tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]
"""Grey icon composited over black, and its alpha footprint (0..1)."""
type Side = Literal["you", "them"]

ELEMENT_BANDS: Final[dict[Element, tuple[tuple[tuple[int, int], ...], bool]]] = {
    # OpenCV hue 0..179 -> (bands, measured?)
    Element.LIGHT: (((14, 30),), True),
    Element.EARTH: (((31, 52),), True),
    Element.ICE: (((92, 125),), True),
    Element.DARK: (((127, 168),), True),
    Element.FIRE: (((0, 8), (171, 179)), True),
}
UNSEEN_FACTOR: Final = 0.6
"""Confidence factor of an element whose colour has not been seen on a capture yet (`assumed` band); every element has
been seen now, so the factor is only used if a band is ever marked unmeasured again."""
SAT_MIN, VAL_MIN = 100, 55
HUE_SPREAD_MAX: Final = 14.0
"""Circular hue spread inside a square (the purple one is a violet-to-magenta gradient, ~8.7)."""
CLASS_MIN_SCORE, CLASS_MIN_MARGIN = 0.60, 0.20
"""Icon match gate: true badges scored >= 0.82 with a margin >= 0.37; coloured buttons on 13 other screens <= 0.44
with a margin <= 0.13 (measured, D64)."""
ICON_SCALES: Final = tuple(float(k) for k in np.linspace(1.05, 1.65, 13))

_SPRITE_PX_PER_REM: Final = 32.0
"""The sprite is drawn at half size: 16 css px per rem x 2 sprite px per css px."""
_CLASS_X_REM: Final = {  # background-position of .<class>:before (Stove HeroDetail CSS, 2026-10-12)
    HeroClass.MAGE: 1.625,
    HeroClass.KNIGHT: 3.1875,
    HeroClass.THIEF: 4.5,
    HeroClass.RANGER: 6.0,
    HeroClass.SOUL_WEAVER: 7.4375,
    HeroClass.WARRIOR: 8.875,
}
_ICON_W_REM, _ICON_H_REM = 1.4375, 1.375


class ArenaScreenError(ValueError):
    """Not an Arena VS screen (or not readable)."""


@dataclass(frozen=True, slots=True)
class Badge:
    side: Side
    x: int
    """Level square: left, top, width, height in pixels."""
    y: int
    w: int
    h: int
    element: Element
    element_confidence: float
    hero_class: HeroClass
    class_confidence: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def confidence(self) -> float:
        return min(self.element_confidence, self.class_confidence)


WORK_HEIGHT: Final = 740
"""Every frame is brought to this height before reading (the badges are measured at 0.5x-2.9x of a 740 px capture;
one size makes the detectors' thresholds mean the same on every capture, and crops get the same size)."""
HEIGHT_SLACK: Final = 6
"""A frame this close to `WORK_HEIGHT` (either way) is not resampled."""
MIN_HEIGHT: Final = 240
"""Frames shorter than this are left alone (a minimised or broken window, nothing to read)."""


def normalise(image: BgrImage) -> BgrImage:
    """The frame at `WORK_HEIGHT`: larger ones shrunk, smaller ones enlarged (SPEC D84). The circle detection of the
    Opponents list counts votes along each circle's edge, so its result depends on the absolute size: measured on the
    user's list capture, 3 rows at 740 px high but 2 at 715 and 700 and none at 546; resampled to 740, all three from
    300 px up to 2160 px."""
    height = image.shape[0]
    if height < MIN_HEIGHT or abs(height - WORK_HEIGHT) <= HEIGHT_SLACK:
        return image
    scale = WORK_HEIGHT / height
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return np.asarray(cv2.resize(image, None, fx=scale, fy=scale, interpolation=interpolation), np.uint8)


def class_templates(sprite_png: bytes) -> dict[HeroClass, Template]:
    """The six class icons cut from the Stove guide sprite (top row)."""
    sprite = cv2.imdecode(np.frombuffer(sprite_png, np.uint8), cv2.IMREAD_UNCHANGED)
    if sprite is None or sprite.ndim != 3 or sprite.shape[2] != 4:
        raise ArenaScreenError("the class icon sprite is not an RGBA image")
    row = sprite[: round(_ICON_H_REM * _SPRITE_PX_PER_REM) + 4]
    alpha = row[:, :, 3]
    # The solid parts are assigned to the CSS window holding their centre, then grown back over their own outline
    # (the faint outlines of neighbouring icons touch).
    count, labels, _, centres = cv2.connectedComponentsWithStats((alpha > 100).astype(np.uint8), connectivity=8)
    width = _ICON_W_REM * _SPRITE_PX_PER_REM
    templates: dict[HeroClass, Template] = {}
    for hero_class, x_rem in _CLASS_X_REM.items():
        x0 = x_rem * _SPRITE_PX_PER_REM
        core = np.zeros(alpha.shape, np.uint8)
        for i in range(1, count):
            if x0 <= centres[i][0] < x0 + width:
                core[labels == i] = 1
        keep = cv2.dilate(core, np.ones((3, 3), np.uint8), iterations=3).astype(bool) & (alpha > 8)
        keep[:, : max(0, int(x0) - 2)] = False
        keep[:, int(x0 + width) + 2 :] = False
        ys, xs = np.nonzero(keep)
        if ys.size == 0:
            raise ArenaScreenError(f"no {hero_class.value} icon in the class icon sprite")
        y1, y2, x1, x2 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        footprint = (alpha[y1:y2, x1:x2].astype(np.float32) / 255) * keep[y1:y2, x1:x2]
        grey = row[y1:y2, x1:x2, :3].astype(np.float32).mean(axis=2) * footprint
        templates[hero_class] = (np.asarray(grey, np.float32), np.asarray(footprint, np.float32))
    return templates


def read_badges(image: BgrImage, templates: Mapping[HeroClass, Template]) -> list[Badge]:
    """Every badge, left side first, each side ordered left to right. Raises ArenaScreenError without any."""
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    badges = []
    for square in _squares(image):
        found = _hero_class(grey, square, templates)
        if found is not None:
            badges.append(_badge(image, square, *found))
    if len(badges) >= 3:  # one badge size per screen
        median = float(np.median([b.h for b in badges]))
        badges = [b for b in badges if abs(b.h - median) <= 0.2 * median]
    if not badges:
        raise ArenaScreenError("no hero badges found: not an Arena VS screen")
    return sorted(badges, key=lambda b: (b.side != "you", b.x))


def hero_crop(image: BgrImage, badge: Badge, others: list[Badge]) -> BgrImage:
    """The hero's model under its badge, with the parts that also lie in another hero's region blanked out (a
    neighbour's pixels would otherwise teach or match the wrong hero; measured in the D64 study)."""
    x0, y0, x1, y1 = _model_region(image, badge)
    crop = image[y0:y1, x0:x1].copy()
    for other in others:
        if other is badge:
            continue
        ox0, oy0, ox1, oy1 = _model_region(image, other)
        ix0, iy0, ix1, iy1 = max(x0, ox0), max(y0, oy0), min(x1, ox1), min(y1, oy1)
        if ix0 < ix1 and iy0 < iy1:
            crop[iy0 - y0 : iy1 - y0, ix0 - x0 : ix1 - x0] = 0
        # the badge itself is UI, not the hero
        bx0, by0 = max(x0, other.x - 2 * other.h), max(y0, other.y)
        bx1, by1 = min(x1, other.x + other.w), min(y1, other.y + other.h)
        if bx0 < bx1 and by0 < by1:
            crop[by0 - y0 : by1 - y0, bx0 - x0 : bx1 - x0] = 0
    return np.asarray(crop, np.uint8)


def _model_region(image: BgrImage, badge: Badge) -> tuple[int, int, int, int]:
    """In square heights from the badge centre: x -2..+4, y +0.6..+10 (the model stands under its badge)."""
    s = badge.h
    height, width = image.shape[:2]
    return (
        max(0, int(badge.cx - 2 * s)),
        max(0, int(badge.cy + 0.6 * s)),
        min(width, int(badge.cx + 4 * s)),
        min(height, int(badge.cy + 10 * s)),
    )


@dataclass(frozen=True, slots=True)
class _Square:
    x: int
    y: int
    w: int
    h: int
    element: Element
    quality: float


def _squares(image: BgrImage) -> list[_Square]:
    height = image.shape[0]
    hsv = np.asarray(cv2.cvtColor(image, cv2.COLOR_BGR2HSV), np.uint8)
    found: list[_Square] = []
    for element, (bands, _) in ELEMENT_BANDS.items():
        mask = _in_bands(hsv, bands) & (hsv[:, :, 1] >= SAT_MIN) & (hsv[:, :, 2] >= VAL_MIN)
        mask8 = mask.astype(np.uint8)
        # Closing joins JPEG speckles; opening cuts thin bridges to same-coloured art right under a square.
        variants = (
            mask8,
            cv2.morphologyEx(mask8, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)),
            cv2.morphologyEx(mask8, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)),
        )
        for variant in variants:
            contours, _ = cv2.findContours(variant, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                if h >= 9 and 0.02 <= h / height <= 0.09:
                    quality = _square_quality(hsv, mask, contour, (x, y, w, h), bands)
                    if quality is not None:
                        found.append(_Square(x, y, w, h, element, quality))
    found.sort(key=lambda s: -s.quality)
    kept: list[_Square] = []
    for square in found:  # the mask variants find the same square several times
        if all(_overlap(square, k) < 0.3 for k in kept):
            kept.append(square)
    return kept


def _square_quality(
    hsv: npt.NDArray[np.uint8],
    mask: npt.NDArray[np.bool_],
    contour: Any,
    rect: tuple[int, int, int, int],
    bands: tuple[tuple[int, int], ...],
) -> float | None:
    """How clearly this region is a level square (0..1), or None when it is not one."""
    x, y, w, h = rect
    aspect = w / h
    hull = cv2.convexHull(contour)
    filled = np.zeros((h, w), np.uint8)
    cv2.drawContours(filled, [hull - np.array([x, y])], -1, 1, thickness=-1)
    inside = filled.astype(bool)
    extent = inside.sum() / (w * h)
    region = hsv[y : y + h, x : x + w]
    colour = mask[y : y + h, x : x + w]
    sat, val = region[:, :, 1], region[:, :, 2]
    median_sat = float(np.median(sat[inside & colour])) if (inside & colour).any() else 255.0
    ink_limit = max(90.0, 0.6 * median_sat)
    white = (val >= 160) & (sat < ink_limit)
    relaxed = _in_bands(region, bands) & (sat >= 50) & (val >= 40)
    coverage = float((relaxed | ((val >= 150) & (sat < ink_limit)))[inside].mean())
    hues = region[:, :, 0][inside & colour]
    spread = _hue_spread(hues) if hues.size else 99.0
    _, _, stats, _ = cv2.connectedComponentsWithStats((white & inside).astype(np.uint8), connectivity=8)
    tall = sum(1 for s in stats[1:] if s[3] >= 0.4 * h and s[4] >= 4)
    if not (
        0.8 <= aspect <= 1.45
        and extent >= 0.8
        and coverage >= 0.9
        and 0.06 <= float(white[inside].mean()) <= 0.5
        and float(colour[inside].mean()) >= 0.4
        and spread <= HUE_SPREAD_MAX
        and 1 <= tall <= 3
    ):
        return None
    return float(
        min(
            1.0,
            (extent - 0.8) / 0.12,
            (coverage - 0.9) / 0.05,
            (HUE_SPREAD_MAX - spread) / 6,
            1 - abs(aspect - 1.12) / 0.5,
        )
    )


def _hero_class(
    grey: npt.NDArray[np.float32], square: _Square, templates: Mapping[HeroClass, Template]
) -> tuple[HeroClass, float] | None:
    s = square.h
    cy = square.y + s / 2
    x1, y1 = max(0, round(square.x - 1.75 * s)), max(0, round(cy - 0.95 * s))
    x2, y2 = min(grey.shape[1], round(square.x + 0.05 * s)), min(grey.shape[0], round(cy + 0.95 * s))
    return class_in_region(grey, (x1, y1, x2, y2), s, templates)


def class_in_region(
    grey: npt.NDArray[np.float32],
    region: tuple[int, int, int, int],
    square_height: int,
    templates: Mapping[HeroClass, Template],
) -> tuple[HeroClass, float] | None:
    """The class icon inside `region` (x1, y1, x2, y2), whose size is judged against the level square's height; None
    unless one icon clearly wins (`CLASS_MIN_SCORE`, `CLASS_MIN_MARGIN`)."""
    s = square_height
    x1, y1, x2, y2 = region
    roi = grey[y1:y2, x1:x2]
    scores: dict[HeroClass, float] = {}
    for hero_class, (icon, footprint) in templates.items():
        best = -1.0
        for k in ICON_SCALES:
            th = max(3, round(k * s))
            tw = max(3, round(icon.shape[1] * th / icon.shape[0]))
            if tw >= roi.shape[1] or th >= roi.shape[0]:
                continue
            template = cv2.resize(icon, (tw, th), interpolation=cv2.INTER_AREA)
            weight = (cv2.resize(footprint, (tw, th), interpolation=cv2.INTER_AREA) > 0.15).astype(np.float32)
            result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED, mask=weight)
            result[~np.isfinite(result)] = -1
            best = max(best, float(result.max()))
        scores[hero_class] = best
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if len(ranked) < 2:
        return None
    (top, first), (_, second) = ranked[0], ranked[1]
    if first < CLASS_MIN_SCORE or first - second < CLASS_MIN_MARGIN:
        return None
    confidence = min(1.0, (first - CLASS_MIN_SCORE) / 0.25) * min(1.0, (first - second - CLASS_MIN_MARGIN) / 0.2)
    return top, round(max(confidence, 0.0), 3)


def level_squares(image: BgrImage) -> list[tuple[int, int, int, int, Element, float]]:
    """Every level square on the screen (x, y, w, h, element, element confidence), whatever the layout."""
    out = []
    for square in _squares(image):
        crop = image[square.y : square.y + square.h, square.x : square.x + square.w]
        hsv = np.asarray(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV), np.uint8)
        saturated = (hsv[:, :, 1] >= SAT_MIN) & (hsv[:, :, 2] >= VAL_MIN)
        bands, measured = ELEMENT_BANDS[square.element]
        share = float(_in_bands(hsv, bands)[saturated].mean()) if saturated.any() else 0.0
        out.append(
            (
                square.x,
                square.y,
                square.w,
                square.h,
                square.element,
                round(share * (1.0 if measured else UNSEEN_FACTOR), 3),
            )
        )
    return out


def _badge(image: BgrImage, square: _Square, hero_class: HeroClass, class_confidence: float) -> Badge:
    crop = image[square.y : square.y + square.h, square.x : square.x + square.w]
    hsv = np.asarray(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV), np.uint8)
    saturated = (hsv[:, :, 1] >= SAT_MIN) & (hsv[:, :, 2] >= VAL_MIN)
    bands, measured = ELEMENT_BANDS[square.element]
    share = float(_in_bands(hsv, bands)[saturated].mean()) if saturated.any() else 0.0
    side: Side = "you" if square.x + square.w / 2 < image.shape[1] / 2 else "them"
    return Badge(
        side=side,
        x=square.x,
        y=square.y,
        w=square.w,
        h=square.h,
        element=square.element,
        element_confidence=round(share * (1.0 if measured else UNSEEN_FACTOR), 3),
        hero_class=hero_class,
        class_confidence=class_confidence,
    )


def _in_bands(hsv: npt.NDArray[np.uint8], bands: tuple[tuple[int, int], ...]) -> npt.NDArray[np.bool_]:
    hue = hsv[:, :, 0]
    mask = np.zeros(hue.shape, bool)
    for low, high in bands:
        mask |= (hue >= low) & (hue <= high)
    return mask


def _hue_spread(hues: npt.NDArray[np.uint8]) -> float:
    angles = hues.astype(np.float64) * (2 * math.pi / 180)
    r = math.hypot(float(np.cos(angles).mean()), float(np.sin(angles).mean()))
    return math.sqrt(max(0.0, -2 * math.log(max(r, 1e-9)))) * 180 / (2 * math.pi)


def _overlap(a: _Square, b: _Square) -> float:
    ix = max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    return inter / float(a.w * a.h + b.w * b.h - inter)
