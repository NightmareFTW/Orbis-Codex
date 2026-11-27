"""What to do next in the Skill reader (SPEC D136): the line under a reading on the badge.

From what is stored for a hero: the skills still to hold, the readings to hold again (cut, unplaced) and the icons
still missing. Pure logic on the store."""

from __future__ import annotations

from collections.abc import Callable

from e7ac.skills.problems import effect_titles, hero_problems
from e7ac.skills.store import SkillStore, StoredSkill
from e7ac.skills.worklist import SKILLS_PER_HERO, slot_marks


def next_step(
    store: SkillStore, code: str, name: str, has_icon: Callable[[StoredSkill], bool] | None = None
) -> tuple[str, bool]:
    """(the line to show, whether the hero is done): "Muwi 2 of 3 - next: hold S1", "Muwi: hold S3 again (cut)",
    "Muwi complete"."""
    skills = store.load(code)
    marks, read = slot_marks(skills)
    problems = hero_problems(skills, effect_titles(store), has_icon)
    if read >= SKILLS_PER_HERO and not problems:
        return f"{name}: all 3 skills read, icons kept", True
    steps = [f"S{i}" for i, m in enumerate(marks, 1) if m == "-"]
    if steps:
        return f"{name} {read} of {SKILLS_PER_HERO} - next: hold {_listed(steps)}", False
    again = sorted({p.skill for p in problems})
    kinds = sorted({p.kind for p in problems})
    return f"{name}: hold again {', '.join(again)} ({'/'.join(kinds)})"[:80], False


def _listed(items: list[str]) -> str:
    """ "S1", "S1 and S2", "S1, S2 and S3"."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
