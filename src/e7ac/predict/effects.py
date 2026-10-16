"""Skill effects for the battle simulation, read automatically from epic7db's effect icons and skill text (SPEC D63).

Generic on purpose (one rule per effect, for every hero): the icon says *which* effect; the value comes from the icon's
description ("by 50%") or MECH-DMG-06; the chance ("50% chance") and duration ("for 2 turns") from the skill text,
else 100% and 2 turns (`assumed`). Buffs go to the caster, or to every ally when the text says "all allies"; debuffs
to the skill's targets and land by MECH-EFF-01. A passive (S2) acts at battle start when its text says so, and a
"lethal damage ... regenerates/revives ... N% Health" passive revives once. An S2 with a cooldown is an active skill.
Survival (SPEC D68): heals and barriers take their amount from Fribbels' skill options (`heal` / `barrier`: share of
the caster's max Health, Attack or Defense, `community`, MECH-HEAL-01) and their receivers from the text (all allies,
the N allies with the lowest Health, the caster); a passive heal or barrier "at the start/end of the turn" acts on each
of the caster's turns; "reviving all dead allies to N% Health" / "revives them with N% Health" resurrects; cleanse and
dispel remove all, one, two or three debuffs / buffs; invincibility blocks all damage. Part 2 (SPEC D74): burn, bleed,
poison (MECH-DOT-01), provoke, silence, counterattack (a buff, or a passive "N% chance to counterattack"). Anything else
is listed as not modelled.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
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
    ("invincible", "invincible", False),
    ("healing", "heal", False),
    ("barrier", "barrier", False),
    ("resurrect", "resurrect", False),
    ("cleanse", "cleanse", False),
    ("dispel", "dispel", True),
    ("burn", "burn", True),
    ("bleed", "bleed", True),
    ("poison", "poison", True),
    ("provoke", "provoke", True),
    ("silence", "silence", True),
    ("counterattack", "counter", False),
)
DOT_ATK: Final = {"burn": 0.6, "bleed": 0.3}
"""MECH-DOT-01 (`assumed`): burn / bleed damage per turn as a share of the caster's Attack, ignoring Defense."""
POISON_MAX_HP: Final = 0.05
"""MECH-DOT-01 (`community`): poison damage per turn as a share of the target's max Health."""
_COUNTS: Final = {"all": 0, "one": 1, "two": 2, "three": 3}
type Scaling = tuple[float, float, float]
"""Share of the caster's max Health, Attack and Defense (Fribbels skill options)."""


@dataclass(frozen=True, slots=True)
class Effect:
    kind: str
    """atk / def / spd (stat change), stun, cr (combat readiness), extra_turn, immortal, immunity, invincible, heal,
    barrier, resurrect, cleanse, dispel."""
    debuff: bool
    value: float
    """Stat change or CR change as a fraction (negative for debuffs); resurrect: Health fraction; 0 when not needed."""
    turns: int
    chance: float
    all_allies: bool = False
    scaling: Scaling = (0.0, 0.0, 0.0)
    """heal / barrier: amount = these shares of the caster's max Health, Attack, Defense."""
    receivers: str = ""
    """heal / barrier / cleanse: "self", "all", or "lowest:N" (the N living allies with the lowest Health);
    resurrect: "all" or "one" (dead allies)."""
    count: int = 0
    """cleanse / dispel: how many debuffs / buffs (0 = all)."""


@dataclass(slots=True)
class Kit:
    on_use: dict[int, list[Effect]] = field(default_factory=dict)
    at_start: list[Effect] = field(default_factory=list)
    on_turn: list[Effect] = field(default_factory=list)
    counter: float = 0.0
    """Passive chance to counterattack with S1 when attacked ("N% chance to counterattack")."""
    """Passive heals / barriers acting on each of the caster's turns."""
    revive: float | None = None
    """HP fraction restored once on lethal damage."""
    revive_effects: list[Effect] = field(default_factory=list)
    unmodelled: list[str] = field(default_factory=list)


def kit_from(skills: dict[int, SkillInfo], amounts: Mapping[int, Mapping[str, Scaling]] | None = None) -> Kit:
    """`amounts`: per skill slot, Fribbels' "heal" / "barrier" scaling (none: those effects are not modelled)."""
    kit = Kit()
    for slot, info in sorted(skills.items()):
        effects = []
        for name, value in info.effects.items():
            effect = _effect(name, value, info.text, (amounts or {}).get(slot, {}))
            if effect is None:
                kit.unmodelled.append(name)
            else:
                effects.append(effect)
        if slot == 2 and not info.cooldown:  # a passive
            revive = _revive(info.text)
            lowered = info.text.lower()
            counter = re.search(r"(\d+)% chance to counterattack", lowered)
            if counter is not None:
                kit.counter = int(counter.group(1)) / 100
                effects = [e for e in effects if e.kind != "counter"]
            if revive is not None:
                kit.revive, kit.revive_effects = revive, [e for e in effects if not e.debuff]
            elif "start of the battle" in lowered:
                kit.at_start = [e for e in effects if not e.debuff]
            elif re.search(r"(?:start|end) of (?:the|each|every) turn", lowered):
                kit.on_turn = [e for e in effects if e.kind in ("heal", "barrier")]
                kit.unmodelled.extend(f"S2 {e.kind}" for e in effects if e.kind not in ("heal", "barrier"))
            elif effects:
                kit.unmodelled.extend(f"S2 {e.kind}" for e in effects)
        else:
            kit.on_use[slot] = effects
    return kit


def _effect(name: str, value: float | None, text: str, amounts: Mapping[str, Scaling]) -> Effect | None:
    lowered = name.lower()
    match = next(((kind, debuff) for key, kind, debuff in _KINDS if lowered.startswith(key)), None)
    if match is None:
        return None
    kind, debuff = match
    turns = re.search(r"for (\d+) turns?", text, re.IGNORECASE)
    duration = int(turns.group(1)) if turns else DEFAULT_TURNS
    if kind in ("heal", "barrier"):
        scaling = amounts.get(kind)
        if scaling is None:
            return None  # no amount known: never guessed
        return Effect(kind, False, 0.0, duration, 1.0, scaling=scaling, receivers=_receivers(text, kind))
    if kind == "resurrect":
        found = re.search(r"reviv\w*[^.]{0,40}?(\d+)% (?:of )?(?:max )?health", text, re.IGNORECASE)
        if found is None:
            return None
        every = re.search(r"all (?:dead|fallen) allies", text, re.IGNORECASE)
        return Effect(kind, False, int(found.group(1)) / 100, 0, 1.0, receivers="all" if every else "one")
    if kind in ("cleanse", "dispel"):
        noun = "debuffs?" if kind == "cleanse" else "buffs?"
        found = re.search(rf"dispel\w*(?: [a-z]+)? (all|one|two|three) {noun}", text, re.IGNORECASE)
        if found is None:
            return None
        count = _COUNTS[found.group(1).lower()]
        receivers = _receivers(text, "debuff") if kind == "cleanse" else ""
        return Effect(kind, debuff, 0.0, 0, 1.0, receivers=receivers, count=count)
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
    chance = re.search(r"(\d+)% chance", text, re.IGNORECASE)
    return Effect(
        kind=kind,
        debuff=debuff,
        value=-(amount or 0.0) if debuff else (amount or 0.0),
        turns=duration,
        chance=int(chance.group(1)) / 100 if chance and debuff else 1.0,
        all_allies=not debuff and "all allies" in text.lower(),
    )


def _receivers(text: str, word: str) -> str:
    """Who receives a heal, barrier or cleanse: from the sentence that names it (`assumed`: "an ally" = the one with
    the lowest Health)."""
    named = rf"\b{word}" if word != "heal" else r"\bheal(?:s|ing|ed)?\b|\brecover"
    sentences = [s for s in re.split(r"(?<=\.)\s", text) if re.search(named, s, re.IGNORECASE)]
    sentence = (sentences[0] if sentences else text).lower()
    lowest = re.search(r"(two|three)? ?all(?:y|ies) with the lowest health", sentence)
    if lowest:
        return f"lowest:{_COUNTS.get(lowest.group(1) or 'one', 1)}"
    if "all allies" in sentence or "all living allies" in sentence:
        return "all"
    if re.search(r"\ban ally\b|\bthe ally\b", sentence):
        return "lowest:1"
    return "self"


def _revive(text: str) -> float | None:
    if not re.search(r"lethal damage|upon death|when .{0,30}(?:dies|is defeated)", text, re.IGNORECASE):
        return None
    found = re.search(r"(?:regenerates?|revives?|resurrect\w*)[^.]{0,40}?(\d+)% (?:of )?(?:max )?health", text, re.I)
    return int(found.group(1)) / 100 if found else None
