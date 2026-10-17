"""Skills read from the game, turned into what the kit readers consume (SPEC D91).

A popup becomes a `SkillInfo` like the ones epic7db gives: its effect panels are the "icons" (name -> the value of
"... by 70%"), so a newer hero that epic7db shows no icons for gets its generic effects (Decrease Defense, Fear, Stun,
Barrier, ...) with the game's own numbers; the text is the game's, taken forward to the fully enhanced skill (the
steps still to come that change a chance or a Combat Readiness amount are added to it, one number at a time, only when
the text has exactly one number they can belong to); the cooldown is the fully enhanced one. Which skill slot a popup is
(1, 2 or 3) is found by its name among epic7db's skills of the hero: a popup shows no slot.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final

from e7ac.catalog.names import normalise_name
from e7ac.skills.store import StoredSkill
from e7ac.sources.epic7db import SkillInfo

_CUT: Final = re.compile(r"-\s*(\d+)\s*turns?\s*cooldown", re.IGNORECASE)
_CHANCE_STEP: Final = re.compile(r"\+\s*(\d+)\s*%\s*effect chance", re.IGNORECASE)
_CR_STEP: Final = re.compile(r"\+\s*(\d+)\s*%\s*combat readiness", re.IGNORECASE)
_CHANCE: Final = re.compile(r"(\d+)%( chance)", re.IGNORECASE)
_CR: Final = re.compile(r"(combat readiness (?:of [a-z ]+ )?by )(\d+)%", re.IGNORECASE)
_BY: Final = re.compile(r"\bby (\d+(?:\.\d+)?)%")


def slot_of(name: str, skills: Mapping[int, SkillInfo]) -> int | None:
    """The slot whose epic7db name is this popup's skill name (exact once normalised), or None."""
    wanted = normalise_name(name)
    found = [slot for slot, info in skills.items() if info.name and normalise_name(info.name) == wanted]
    return found[0] if len(found) == 1 else None


def remaining(stored: StoredSkill) -> list[str]:
    """The enhancement steps above the level the popup was read at."""
    level = stored.level or 0
    return [description for step, description in stored.enhance if step > level]


def to_maximum(text: str, steps: Sequence[str]) -> tuple[str, list[str]]:
    """The text with the steps still to come applied where that is unambiguous: "+10% effect chance" to the one "N%
    chance" of the text, "+3% Combat Readiness" to the one "Combat Readiness by N%". (text, the steps that change a
    number of the text and could not be applied: healing, barrier strength, an unclear chance)."""
    left: list[str] = []
    for step in steps:
        chance, cr = _CHANCE_STEP.search(step), _CR_STEP.search(step)
        if chance and len(_CHANCE.findall(text)) == 1:
            text = _add(_CHANCE, text, int(chance.group(1)), "")
        elif cr and len(_CR.findall(text)) == 1:
            text = _add(_CR, text, int(cr.group(1)), "cr")
        elif chance or cr or not (_CUT.search(step) or "damage dealt" in step.lower()):
            left.append(step)  # damage and cooldown steps are not numbers of the text; the rest should have been
    return text, left


def _add(pattern: re.Pattern[str], text: str, more: int, kind: str) -> str:
    """`more` points added to the one number the pattern finds."""

    def bump(m: re.Match[str]) -> str:
        if kind == "cr":
            return f"{m.group(1)}{int(m.group(2)) + more}%"
        return f"{int(m.group(1)) + more}%{m.group(2)}"

    return pattern.sub(bump, text)


def skill_info(stored: StoredSkill, slot: int, base: SkillInfo | None = None) -> SkillInfo:
    """The popup as a `SkillInfo` (epic7db's soulburn, keywords and so on kept when `base` has them)."""
    level = stored.level
    known = level is not None  # no badge read: the numbers are taken as shown, never moved forward
    cut_now = sum(int(n) for step, d in stored.enhance if known and step <= (level or 0) for n in _CUT.findall(d))
    cut_later = sum(int(n) for step, d in stored.enhance if known and step > (level or 0) for n in _CUT.findall(d))
    if stored.cooldown is not None:
        cooldown: int | None = stored.cooldown + cut_now
        cooldown_max: int | None = stored.cooldown - cut_later
    else:
        cooldown = cooldown_max = 0 if slot == 1 else None
    text = to_maximum(stored.text, remaining(stored))[0] if known else stored.text
    effects = {panel.title: _value(panel.text) for panel in stored.effects}
    souls = next((n for kind, n in stored.gains if kind.lower() == "soul"), None)
    return SkillInfo(
        slot,
        cooldown,
        effects,
        text,
        name=stored.name,
        soulburn=stored.soulburn,
        souls=souls,
        enhance=stored.enhance,
        keywords=base.keywords if base else (),
        effect_titles={p.title: p.text for p in stored.effects},
        captured=True,
        level=stored.level,
        cooldown_max=cooldown_max,
    )


def merge(skills: Mapping[int, SkillInfo], captured: Sequence[StoredSkill]) -> dict[int, SkillInfo]:
    """epic7db's skills of a hero with the ones read from the game in their place. A popup whose slot cannot be told
    (no epic7db page, another name) is left out here; `export` and the worklist still list it."""
    out = dict(skills)
    for stored in captured:
        slot = slot_of(stored.name, skills)
        if slot is not None:
            out[slot] = skill_info(stored, slot, skills[slot])
    return out


def _value(text: str) -> float | None:
    found = _BY.search(text)
    return round(float(found.group(1)) / 100, 6) if found else None


def unplaced(skills: Mapping[int, SkillInfo], captured: Sequence[StoredSkill]) -> list[StoredSkill]:
    return [s for s in captured if slot_of(s.name, skills) is None]
