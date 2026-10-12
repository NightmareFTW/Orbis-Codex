"""Skill effects for the battle simulation, read automatically from epic7db's effect icons and skill text (SPEC D63).

Generic on purpose (one rule per effect, for every hero): the icon says *which* effect; the value comes from the icon's
description ("by 50%") or MECH-DMG-06; the chance ("50% chance") and duration ("for 2 turns") from the skill text,
else 100% and 2 turns (`assumed`). Buffs go to the caster, or to every ally when the text says "all allies"; debuffs
to the skill's targets and land by MECH-EFF-01. A passive (S2) acts at battle start when its text says so, and a
"lethal damage ... regenerates/revives ... N% Health" passive revives once. Anything else is listed as not modelled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from e7ac.sources.epic7db import SkillInfo

DEFAULT_TURNS: Final = 2
"""Duration when the skill text gives none (`assumed`)."""
_STAT_DEFAULTS: Final = {  # MECH-DMG-06 (community) when the icon has no value
    ("atk", False): 0.5,
    ("atk", True): 0.5,
    ("def", False): 0.6,
    ("def", True): 0.7,
}
_KINDS: Final = (  # effect name (epic7db) -> kind, debuff?
    ("increase attack", "atk", False),
    ("decrease attack", "atk", True),
    ("increase defense", "def", False),
    ("decrease defense", "def", True),
    ("increase speed", "spd", False),
    ("decrease speed", "spd", True),
    ("stun", "stun", True),
    ("sleep", "stun", True),
    ("increase combat readiness", "cr", False),
    ("decrease combat readiness", "cr", True),
    ("extra turn", "extra_turn", False),
    ("immortal", "immortal", False),
    ("immunity", "immunity", False),
)


@dataclass(frozen=True, slots=True)
class Effect:
    kind: str
    """atk / def / spd (stat change), stun, cr (combat readiness), extra_turn, immortal, immunity."""
    debuff: bool
    value: float
    """Stat change or CR change as a fraction (negative for debuffs); 0 when not needed."""
    turns: int
    chance: float
    all_allies: bool = False


@dataclass(slots=True)
class Kit:
    on_use: dict[int, list[Effect]] = field(default_factory=dict)
    at_start: list[Effect] = field(default_factory=list)
    revive: float | None = None
    """HP fraction restored once on lethal damage."""
    revive_effects: list[Effect] = field(default_factory=list)
    unmodelled: list[str] = field(default_factory=list)


def kit_from(skills: dict[int, SkillInfo]) -> Kit:
    kit = Kit()
    for slot, info in sorted(skills.items()):
        effects = []
        for name, value in info.effects.items():
            effect = _effect(name, value, info.text)
            if effect is None:
                kit.unmodelled.append(name)
            else:
                effects.append(effect)
        if slot == 2:
            revive = _revive(info.text)
            if revive is not None:
                kit.revive, kit.revive_effects = revive, [e for e in effects if not e.debuff]
            elif "start of the battle" in info.text.lower():
                kit.at_start = [e for e in effects if not e.debuff]
            elif effects:
                kit.unmodelled.extend(f"S2 {e.kind}" for e in effects)
        else:
            kit.on_use[slot] = effects
    return kit


def _effect(name: str, value: float | None, text: str) -> Effect | None:
    lowered = name.lower()
    match = next(((kind, debuff) for key, kind, debuff in _KINDS if lowered.startswith(key)), None)
    if match is None:
        return None
    kind, debuff = match
    amount = value
    if kind in ("atk", "def"):
        greater = "greater" in lowered or "(greater)" in lowered
        amount = value if value is not None else (0.75 if greater and kind == "atk" else _STAT_DEFAULTS[(kind, debuff)])
    elif kind == "spd" and value is None:
        return None  # no documented default: not guessed
    elif kind == "cr":
        found = re.search(r"combat readiness[^.%]{0,60}?by (\d+)%", text, re.IGNORECASE)
        if found is None:
            return None
        amount = int(found.group(1)) / 100
    turns = re.search(r"for (\d+) turns?", text, re.IGNORECASE)
    chance = re.search(r"(\d+)% chance", text, re.IGNORECASE)
    return Effect(
        kind=kind,
        debuff=debuff,
        value=-(amount or 0.0) if debuff else (amount or 0.0),
        turns=int(turns.group(1)) if turns else DEFAULT_TURNS,
        chance=int(chance.group(1)) / 100 if chance and debuff else 1.0,
        all_allies=not debuff and "all allies" in text.lower(),
    )


def _revive(text: str) -> float | None:
    if not re.search(r"lethal damage|upon death|when .{0,30}(?:dies|is defeated)", text, re.IGNORECASE):
        return None
    found = re.search(r"(?:regenerates?|revives?|resurrect\w*)[^.]{0,40}?(\d+)% (?:of )?(?:max )?health", text, re.I)
    return int(found.group(1)) / 100 if found else None
