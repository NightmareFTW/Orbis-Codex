"""A hero's skills as the reader keeps them, in the hero's own order (SPEC D100): which of S1, S2, S3 is read, what each
one came to (level, cooldown or its note, steps, effects) and which is still to read. UI-free: the page shows it under
the table, `e7 skills show` could print it."""

from __future__ import annotations

from collections.abc import Collection, Sequence

from e7ac.skills.problems import skill_problems
from e7ac.skills.store import StoredSkill
from e7ac.skills.worklist import SKILLS_PER_HERO

EXCERPT: int = 110
"""Characters of the game's text shown for a skill."""


def skill_line(skill: StoredSkill, titles: Collection[str] = ()) -> list[str]:
    """Two lines for one skill: what it is, then the start of the game's text."""
    kind = " (passive)" if skill.passive else ""
    level = f"+{skill.level}" if skill.level is not None else "+0"
    cooldown = f"cooldown {skill.cooldown}" if skill.cooldown else skill.cooldown_note or "no cooldown"
    parts = [level, cooldown, f"{len(skill.enhance)} enhancement steps"]
    if skill.soulburn:
        parts.append("soulburn")
    if skill.effects:
        parts.append("effects: " + ", ".join(e.title for e in skill.effects))
    if found := skill_problems(skill, titles):
        parts.append(
            "PROBLEM: " + "; ".join(p.detail for p in found) + (" - hold it again" if found[0].clearable else "")
        )
    text = skill.text if len(skill.text) <= EXCERPT else skill.text[:EXCERPT].rstrip() + "..."
    return [f"{skill.name}{kind}  |  " + "  |  ".join(parts), f"      {text}"]


def hero_summary(name: str, code: str, skills: Sequence[StoredSkill], titles: Collection[str] = ()) -> str:
    """The text for a hero: a header, S1, S2, S3 each read or "not read yet", and the skills read whose place is not
    known ("?")."""
    placed = {s.slot: s for s in skills if s.slot in (1, 2, 3)}
    loose = [s for s in skills if s.slot not in (1, 2, 3)]
    read = len(placed) + len(loose)
    lines = [f"{name} ({code}): {min(read, SKILLS_PER_HERO)} of {SKILLS_PER_HERO} skills read"]
    for slot in range(1, SKILLS_PER_HERO + 1):
        skill = placed.get(slot)
        if skill is None:
            lines.append(f"S{slot}  - not read yet")
        else:
            first, second = skill_line(skill, titles)
            lines += [f"S{slot}  {first}", second]
    for skill in loose:
        first, second = skill_line(skill, titles)
        lines += [f"?   {first}", second, "      (epic7db did not place it among the hero's skills: it stays unplaced)"]
    return "\n".join(lines)
