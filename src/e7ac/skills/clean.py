"""Readings that are not skills (SPEC D104): what the reader stored before it learned to tell the game from the
program's own window and badge, or an enhancement step from a title. `e7 skills clean` finds them by what they are:
the text of the Orbis window or of its badge ("Read: Notos -", "Hero of the day"), a name that is a line of the
window's detail panel ("S3 Sword of Requiem |") or the first step of a table ("+3 Fighting Spirit at the start of the
first battle"), and no description at all. Nothing a real popup can look like is touched."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from e7ac.skills.own_ui import BADGE_LINE, WINDOW_MARKERS
from e7ac.skills.store import SkillStore, StoredSkill

NOT_A_NAME: Final = re.compile(r"^(?:S[123]\b|[+-]\s*\d)|\|\s*$")
"""A detail line of the window ("S3  Name |"), a step ("+3 ...", "- ...") or a cost separator at the end."""
SHORTEST: Final = 25
"""A skill description shorter than this, with no steps and no effects, is not one."""


@dataclass(frozen=True, slots=True)
class Junk:
    code: str
    name: str
    why: str


def why_junk(skill: StoredSkill) -> str | None:
    text = skill.text.lower()
    if any(marker in text for marker in WINDOW_MARKERS) or BADGE_LINE.match(skill.text.strip()):
        return "its text is the Orbis window's or the badge's"
    if NOT_A_NAME.search(skill.name.strip()):
        return "its name is a line of the Orbis window or an enhancement step"
    if len(skill.text.strip()) < SHORTEST and not skill.enhance and not skill.effects:
        return "it has no description"
    return None


def find(store: SkillStore) -> list[Junk]:
    return [
        Junk(code, skill.name, why)
        for code in store.heroes()
        for skill in store.load(code)
        if (why := why_junk(skill)) is not None
    ]


def clean(store: SkillStore) -> list[Junk]:
    """Drop every reading `find` lists, and the effects only they had shown; returns what went."""
    found = find(store)
    for item in found:
        store.forget(item.code, item.name)
    return found
