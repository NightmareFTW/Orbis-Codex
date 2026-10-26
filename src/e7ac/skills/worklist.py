"""What is still missing from the skills read from the game (SPEC D91), most worth reading first.

A hero has three skills; it is complete when three different skills were read. The order: the heroes met in your
Arena (the sightings of the watch), then the heroes of epic7db's RTA tier list (best tier first: the PvP meta, a
proxy until more of your Arena is seen), then the rest by name. Both orders are inputs, so this is pure."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from e7ac.skills.store import SkillStore, StoredSkill

SKILLS_PER_HERO = 3


@dataclass(frozen=True, slots=True)
class SkillRow:
    code: str
    name: str
    why: str
    """Why it is where it is: "met 16x in your Arena", "RTA tier list #4", ""."""
    read: int
    """Different skills read (0 to 3)."""
    slots: tuple[str, ...] = ()
    """What each of the three skills, in the hero's order (S1, S2, S3), came to: "S" an active skill read, "P" a passive
    read, "?" a skill read whose slot is not known, "-" not read. Empty: only the count is known."""

    @property
    def complete(self) -> bool:
        return self.read >= SKILLS_PER_HERO

    @property
    def marks(self) -> str:
        if self.slots:
            return " ".join(self.slots)
        return " ".join("S" if i < self.read else "-" for i in range(SKILLS_PER_HERO))


def slot_marks(skills: Sequence[StoredSkill]) -> tuple[tuple[str, ...], int]:
    """(the marks of the three slots, skills read): a placed skill sits at its own slot, one that is not placed takes
    the first slot nobody has, as "?"; two popups of one slot (an OCR variant of its name) count once."""
    marks = ["-"] * SKILLS_PER_HERO
    loose = []
    for skill in skills:
        if skill.slot in (1, 2, 3):
            marks[skill.slot - 1] = "P" if skill.passive else "S"
        else:
            loose.append(skill)
    for _ in {s.key for s in loose}:
        free = next((i for i, m in enumerate(marks) if m == "-"), None)
        if free is not None:
            marks[free] = "?"
    return tuple(marks), sum(m != "-" for m in marks)


def worklist(
    heroes: Mapping[str, str],
    seen: Sequence[tuple[str, int]],
    tiers: Sequence[str],
    store: SkillStore,
) -> list[SkillRow]:
    """`heroes`: code -> name. `seen`: (code, teams) most met first. `tiers`: codes in the RTA tier list's order."""
    order: list[tuple[str, str]] = []
    placed: set[str] = set()
    for code, count in seen:
        if code in heroes and code not in placed:
            order.append((code, f"met {count}x in your Arena"))
            placed.add(code)
    for rank, code in enumerate(tiers, 1):
        if code in heroes and code not in placed:
            order.append((code, f"RTA tier list #{rank}"))
            placed.add(code)
    order += [(code, "") for code in sorted(heroes, key=lambda c: heroes[c].lower()) if code not in placed]
    rows = []
    for code, why in order:
        marks, read = slot_marks(store.load(code))
        rows.append(SkillRow(code, heroes[code], why, read, marks))
    return rows


def progress(rows: Sequence[SkillRow]) -> tuple[int, int, int, int]:
    """(heroes complete, heroes with something read, skills read, heroes in all)."""
    return (
        sum(r.complete for r in rows),
        sum(r.read > 0 for r in rows),
        sum(r.read for r in rows),
        len(rows),
    )
