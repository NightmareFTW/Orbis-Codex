"""Which hero a skill popup belongs to (SPEC D97).

A held skill's popup covers the hero's name, so what the screen still shows of it is the start of the name ("Spirit Ey",
"Harsett"), or nothing. The name is whole on the hero's own screen, before a skill is held, so the reader remembers the
hero it saw there and uses it for the popups that follow. The evidence, strongest first:

1. the hero the user named (the Hero box): taken, unless the screen shows another hero clearly (the box was not changed
   for the next hero);
2. a name on the popup that is a whole catalog hero (exact or the margin rule) is that hero, unless it is just the
   start of the remembered hero's name ("Karin" is the whole of one hero and the start of nothing else, but "Spirit
   Ey" is the start of "Spirit Eye Celine");
3. the start of a name that fits exactly one hero (at least `MIN_START` letters);
4. no usable name: the hero remembered from the hero screen, if it was seen within `REMEMBER` seconds.

A name that is neither whole nor a start is never turned into a hero by closeness (the margin rule is the only
closeness there is). Pure logic, no screen and no clock of its own."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from e7ac.catalog.names import normalise_name
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.roster.screen_import import resolve_hero_code

REMEMBER: Final = 600.0
"""Seconds a hero seen on its own screen is still taken for the hero of the next popups."""
MIN_START: Final = 5
"""Letters the start of a name needs before it names a hero (shorter ones fit too many)."""


@dataclass(frozen=True, slots=True)
class Decision:
    code: str | None
    problem: str = ""
    how: str = ""
    """"chosen", "screen name", "start of the name" or "hero screen" (what the hero was taken from)."""


class HeroMemory:
    """The hero last seen on its own screen, and when."""

    def __init__(self) -> None:
        self._code: str | None = None
        self._seen = 0.0

    def see(self, code: str, now: float) -> None:
        self._code, self._seen = code, now

    def recall(self, now: float) -> str | None:
        return self._code if self._code is not None and now - self._seen <= REMEMBER else None


def starts(read: str, name: str) -> bool:
    """Whether the text read is the beginning of this name (or all of it)."""
    text, whole = normalise_name(read), normalise_name(name)
    return len(text) >= 3 and whole.startswith(text)


def hero_starting_with(read: str, heroes: Mapping[str, ResolvedEntity]) -> str | None:
    """The one hero whose name begins with the text read, when it has `MIN_START` letters or more; None if there are
    none or several."""
    text = normalise_name(read)
    if sum(c.isalpha() for c in text) < MIN_START:
        return None
    found = {code for code, hero in heroes.items() if hero.name and normalise_name(hero.name).startswith(text)}
    return found.pop() if len(found) == 1 else None


def decide(
    read: str | None,
    heroes: Mapping[str, ResolvedEntity],
    chosen: str | None = None,
    remembered: str | None = None,
) -> Decision:
    """The hero of a popup from the name on it (`read`), the hero named by the user and the hero remembered."""
    whole, problem = resolve_hero_code(read, heroes) if read else (None, "the hero name was not read")
    named = whole or (hero_starting_with(read, heroes) if read else None)
    how = "screen name" if whole else "start of the name"

    def fits(code: str) -> bool:
        return bool(read) and starts(read or "", heroes[code].name or "")

    if chosen:
        code, why = (chosen, "") if chosen in heroes else resolve_hero_code(chosen, heroes)
        if code is None:
            return Decision(None, why)
        if named is not None and named != code and not fits(code):
            return Decision(
                None,
                f"the screen shows {heroes[named].name!r} but the hero chosen is {heroes[code].name!r}: "
                "choose the hero you opened (or clear the choice to use the name on the screen)",
            )
        return Decision(code, "", "chosen")
    if remembered is not None and remembered in heroes and (named is None or named == remembered or fits(remembered)):
        return Decision(remembered, "", "hero screen")
    if named is not None:
        return Decision(named, "", how)
    return Decision(
        None,
        problem
        if read and problem
        else "the hero name was not read: open the hero's own screen first (the name is read there) or name the hero",
    )
