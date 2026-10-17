"""The Opponents list: faces and badges together (SPEC D82).

A face is matched against the icons of every hero in the catalog; its badge says the hero's element and class. The two
are independent readings of the same hero, so they check each other:
- a recognised face whose catalog shape equals the badge's: kept (the usual case: 12 of 12 on the user's capture);
- a recognised face whose shape differs from a clearly read badge: not trusted. The heroes with the badge's shape are
  searched for the face (the group rule); found, that hero; not found, the face stays unknown, never the wrong hero;
- a face that was not recognised: searched among the heroes with the badge's shape (D70's rule for the Attack Guide);
- no badge read: the face match alone, as before.
The badge's shape is kept for every face, recognised or not.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from e7ac.catalog.resolve import ResolvedEntity
from e7ac.vision.arena_guide import Face
from e7ac.vision.arena_list import ListBadge

MIN_BADGE: Final = 0.5
"""Smallest confidence of the element and of the class for a badge to be trusted over a face match."""

type Shape = tuple[str, str]
type Among = Callable[[Face, Iterable[str]], tuple[str | None, float, float]]


@dataclass(frozen=True, slots=True)
class ListHero:
    code: str | None
    shape: Shape | None
    """(element, class) from the badge; None when the badge was not clearly read."""
    note: str
    """"" when nothing is worth saying, else what the badge changed."""
    face: Face


def hero_shape(hero: ResolvedEntity) -> Shape | None:
    element, role = hero.value("element"), hero.value("role")
    return (str(element), str(role)) if element and role else None


def resolve_list(
    faces: Sequence[Sequence[Face]],
    badges: Sequence[Sequence[ListBadge]],
    heroes: Mapping[str, ResolvedEntity],
    among: Among,
    name: Callable[[str], str] = str,
) -> list[list[ListHero]]:
    """One `ListHero` per face, rows and faces in the order given."""
    groups: dict[Shape, list[str]] = {}
    for code, hero in heroes.items():
        shape = hero_shape(hero)
        if shape is not None:
            groups.setdefault(shape, []).append(code)
    out: list[list[ListHero]] = []
    for face_row, badge_row in zip(faces, badges, strict=True):
        row = []
        for face, badge in zip(face_row, badge_row, strict=True):
            shape = badge.shape if min(badge.element_confidence, badge.class_confidence) >= MIN_BADGE else None
            row.append(_one(face, shape, heroes, groups, among, name))
        out.append(row)
    return out


def _one(
    face: Face,
    shape: Shape | None,
    heroes: Mapping[str, ResolvedEntity],
    groups: Mapping[Shape, list[str]],
    among: Among,
    name: Callable[[str], str],
) -> ListHero:
    if shape is None:
        return ListHero(face.code, None, "", face)
    group = groups.get(shape, [])
    label = f"{shape[0]} {shape[1]}"
    if face.code is not None:
        own = hero_shape(heroes[face.code]) if face.code in heroes else None
        if own == shape or own is None:
            return ListHero(face.code, shape, "", face)
        code, _, _ = among(face, group)
        note = f"the face looked like {name(face.code)}, but the badge says {label}"
        if code is not None:
            return ListHero(code, shape, f"{note}: {name(code)}", face)
        return ListHero(None, shape, f"{note}: left unknown", face)
    code, score, margin = among(face, group)
    if code is not None:
        return ListHero(
            code, shape, f"{name(code)} among the {len(group)} {label} heroes ({score:.2f}, {margin:.2f} ahead)", face
        )
    return ListHero(None, shape, "", face)
