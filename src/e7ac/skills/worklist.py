"""What is still missing from the skills read from the game (SPEC D91), most worth reading first.

A hero has three skills; it is complete when three different skills were read. The order: the heroes met in your
Arena (the sightings of the watch), then the heroes of epic7db's RTA tier list (best tier first: the PvP meta, a
proxy until more of your Arena is seen), then the rest by name. Both orders are inputs, so this is pure."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from e7ac.skills.store import SkillStore

SKILLS_PER_HERO = 3


@dataclass(frozen=True, slots=True)
class SkillRow:
    code: str
    name: str
    why: str
    """Why it is where it is: "met 16x in your Arena", "RTA tier list #4", ""."""
    read: int
    """Different skills read (0 to 3)."""

    @property
    def complete(self) -> bool:
        return self.read >= SKILLS_PER_HERO

    @property
    def marks(self) -> str:
        return " ".join("S" if i < self.read else "-" for i in range(SKILLS_PER_HERO))


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
    return [
        SkillRow(code, heroes[code], why, min(len({s.key for s in store.load(code)}), SKILLS_PER_HERO))
        for code, why in order
    ]


def progress(rows: Sequence[SkillRow]) -> tuple[int, int, int, int]:
    """(heroes complete, heroes with something read, skills read, heroes in all)."""
    return (
        sum(r.complete for r in rows),
        sum(r.read > 0 for r in rows),
        sum(r.read for r in rows),
        len(rows),
    )
