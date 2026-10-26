"""Every reading that has a problem, in one place (SPEC D107): what the Skill reader page marks and its button clears.

Three kinds, all judged from what is stored:
- "junk": not a skill at all (the Orbis window's or the badge's text, a step taken for a title, no description:
  `skills.clean`);
- "cut": a description or an effect that stops where a sentence cannot, an effect with no description or a title that
  is still open (`skills.completeness`);
- "unplaced": a real skill whose place among the hero's three is not known (epic7db's names did not place it).
Junk and cut readings are what the button drops, to be held again; an unplaced one is only shown. Pure logic on the
store."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Final

from e7ac.skills.clean import why_junk
from e7ac.skills.completeness import cut_parts, holds_title, stray_badge
from e7ac.skills.store import SkillStore, StoredSkill

CLEARABLE: Final = frozenset({"junk", "cut"})
"""The kinds the button drops: what is wrong is in the reading, so reading it again is the cure."""


@dataclass(frozen=True, slots=True)
class Problem:
    code: str
    skill: str
    kind: str
    """"junk", "cut" or "unplaced"."""
    detail: str

    @property
    def clearable(self) -> bool:
        return self.kind in CLEARABLE

    def line(self) -> str:
        return f"{self.skill}: {self.detail}"


def skill_problems(skill: StoredSkill, titles: Collection[str] = ()) -> list[Problem]:
    """The problems of one stored skill (a junk reading is only that: the rest of it means nothing). `titles`: the
    effects known, to find a panel that swallowed the next one."""
    if (why := why_junk(skill)) is not None:
        return [Problem(skill.hero, skill.name, "junk", why)]
    found = [Problem(skill.hero, skill.name, "cut", f"{part} looks cut") for part in cut_parts(skill)]
    if (stray := stray_badge(skill.text)) is not None:
        found.append(Problem(skill.hero, skill.name, "cut", f"its description ends with a stray {stray!r} (a badge)"))
    for panel in skill.effects:
        if (other := holds_title(panel.text, titles)) is not None:
            found.append(Problem(skill.hero, skill.name, "cut", f"effect {panel.title} also holds the text of {other}"))
    if skill.slot not in (1, 2, 3):
        found.append(Problem(skill.hero, skill.name, "unplaced", "its place among the hero's skills is not known"))
    return found


def hero_problems(skills: Sequence[StoredSkill], titles: Collection[str] = ()) -> tuple[Problem, ...]:
    return tuple(p for skill in skills for p in skill_problems(skill, titles))


def effect_titles(store: SkillStore) -> list[str]:
    return [str(entry.get("title", key)) for key, entry in store.effects().items()]


def find_problems(store: SkillStore) -> list[Problem]:
    titles = effect_titles(store)
    return [p for code in store.heroes() for p in hero_problems(store.load(code), titles)]


def clear_problems(store: SkillStore) -> list[Problem]:
    """Drop every skill that has a junk or cut problem (and the effects only it had shown), so it can be held again;
    the skills read well stay. Returns the problems of what went."""
    gone = [p for p in find_problems(store) if p.clearable]
    for code, name in dict.fromkeys((p.code, p.skill) for p in gone):
        store.forget(code, name)
    return gone
