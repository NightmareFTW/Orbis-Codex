"""Does a text read from the game look cut? (SPEC D106)

The reader sometimes loses the last wrapped line of a description or of an effect panel: "Decreases Attack and Speed
by" instead of "... by 30%.". A game text ends with a full stop (the effect lists are the exception and end on a word),
so a text that stops on a word that needs a continuation ("by", "of", "and"...), on a number or on a comma is cut. This
is judged from the text alone, so it also finds what was stored before the reader was fixed. Pure logic."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from e7ac.skills.store import SkillStore, StoredSkill

DANGLING: Final = frozenset(
    {
        *("a", "an", "and", "as", "at", "by", "for", "from", "if", "in", "into", "is", "of", "on", "or"),
        *("than", "that", "the", "then", "to", "when", "whenever", "which", "while", "whose", "with"),
    }
)
"""Words a sentence cannot end on."""
_END: Final = re.compile(r"[.!?)\]\"']\s*$")


def looks_cut(text: str) -> bool:
    """Whether the text stops where a sentence cannot: no full stop, and the last word needs a continuation (or the
    last thing is a number or a comma)."""
    stripped = text.strip()
    if not stripped or _END.search(stripped):
        return False
    if stripped[-1] in ",;:(":
        return True
    last = re.sub(r"[^\w%]", "", stripped.split()[-1]).lower()
    return last in DANGLING or bool(re.fullmatch(r"[\d.,]+%?", last))


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower().rstrip(".")


def fuller(old: str, new: str) -> str:
    """The text to keep when a skill or an effect is read again: the new one, unless it is only the beginning of the
    old one, or it looks cut and the old one does not (a worse reading never replaces a better one)."""
    if old and new != old:
        if len(_plain(old)) > len(_plain(new)) and _plain(old).startswith(_plain(new)):
            return old
        if looks_cut(new) and not looks_cut(old):
            return old
    return new


@dataclass(frozen=True, slots=True)
class Cut:
    hero: str
    skill: str
    part: str
    """"description", "soulburn" or "effect <title>"."""


def cut_parts(skill: StoredSkill) -> list[str]:
    """The parts of a stored skill that look cut."""
    found = ["description"] if looks_cut(skill.text) else []
    found += ["soulburn"] if skill.soulburn and looks_cut(skill.soulburn) else []
    return found + [f"effect {e.title}" for e in skill.effects if _bad(e.title, e.text)]


def _bad(title: str, text: str) -> bool:
    """An effect description that is cut, empty, or whose title is still open ("Energy Depletion (Unique")."""
    return looks_cut(text) or not text.strip() or title.count("(") != title.count(")")


def find_cut(store: SkillStore) -> list[Cut]:
    """Every skill part in the store that looks cut."""
    return [Cut(code, s.name, part) for code in store.heroes() for s in store.load(code) for part in cut_parts(s)]


def cut_effects(store: SkillStore) -> dict[str, list[str]]:
    """Effects whose stored description looks cut -> the heroes it was seen on (the codes)."""
    return {
        str(entry.get("title", key)): [str(h) for h in entry.get("heroes", [])]
        for key, entry in store.effects().items()
        if _bad(str(entry.get("title", key)), str(entry.get("text", "")))
    }


def titles(parts: Iterable[str]) -> str:
    return ", ".join(parts)
