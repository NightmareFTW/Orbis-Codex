"""Equipment-set effects: split the official Stove effect text into static stat bonuses and in-combat effects.

MECH-GEAR-06/07, MECH-STAT-07. The magnitudes come from the official text; that ATK/HP/DEF/SPD bonuses are a
percentage of the hero's *base* stat (while rate stats are additive) is community knowledge from Fribbels'
StatCalculator — so the resulting facts are `community`, never `verified`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from pydantic import JsonValue

from e7ac.catalog.facts import EntityType, Fact
from e7ac.domain.codes import DataStatus, SourceId, Stat

_STAT_NAMES: Final = {
    "attack": Stat.ATK,
    "health": Stat.HP,
    "defense": Stat.DEF,
    "speed": Stat.SPEED,
    "critical hit chance": Stat.CRIT_CHANCE,
    "critical hit damage": Stat.CRIT_DAMAGE,
    "effectiveness": Stat.EFFECTIVENESS,
    "effect resistance": Stat.EFFECT_RESISTANCE,
    "dual attack chance": Stat.DUAL_ATTACK,
}
_NAME_ALT: Final = "|".join(sorted(map(re.escape, _STAT_NAMES), key=len, reverse=True))
_PATTERNS: Final = (
    re.compile(rf"^(?P<stat>{_NAME_ALT}) increases by (?P<value>\d+(?:\.\d+)?)%$", re.IGNORECASE),
    re.compile(rf"^(?P<sign>increases|decreases) (?P<stat>{_NAME_ALT}) by (?P<value>\d+(?:\.\d+)?)%$", re.IGNORECASE),
)
_OF_BASE: Final = frozenset({Stat.ATK, Stat.HP, Stat.DEF, Stat.SPEED})
NOTE: Final = "magnitude from the official Stove text; base-relative application per Fribbels StatCalculator"


@dataclass(frozen=True, slots=True)
class StaticBonus:
    stat: Stat
    value: float
    """Fraction: +0.45 = +45%."""
    of_base: bool
    """True: percentage of the hero's base stat (ATK/HP/DEF/SPD). False: added to the rate stat."""

    def to_json(self) -> dict[str, JsonValue]:
        return {"stat": self.stat.value, "value": self.value, "of_base": self.of_base}


@dataclass(frozen=True, slots=True)
class ParsedSetEffect:
    static: tuple[StaticBonus, ...]
    combat_clauses: tuple[str, ...]

    @property
    def kind(self) -> str:
        if self.static and self.combat_clauses:
            return "mixed"
        return "static" if self.static else "combat"


def parse_effect_text(text: str) -> ParsedSetEffect:
    static: list[StaticBonus] = []
    combat: list[str] = []
    for clause in _clauses(text):
        bonus = _match_static(clause)
        if bonus is None:
            combat.append(clause)
        else:
            static.append(bonus)
    return ParsedSetEffect(static=tuple(static), combat_clauses=tuple(combat))


def set_facts(set_code: str, effect_text: str) -> list[Fact]:
    """Derived facts for one set (source: the official text, interpreted -> community)."""
    parsed = parse_effect_text(effect_text)

    def fact(field: str, value: JsonValue) -> Fact:
        return Fact(
            entity_type=EntityType.SET,
            entity_id=set_code,
            field=field,
            value=value,
            source=SourceId.STOVE,
            status=DataStatus.COMMUNITY,
            note=NOTE,
        )

    static_json: list[JsonValue] = [b.to_json() for b in parsed.static]
    combat_json: list[JsonValue] = list(parsed.combat_clauses)
    return [fact("kind", parsed.kind), fact("static_bonus", static_json), fact("combat_effects", combat_json)]


def _clauses(text: str) -> list[str]:
    sentences = [s.strip() for s in re.split(r"(?<=\.)\s+", text.strip()) if s.strip()]
    clauses: list[str] = []
    for sentence in sentences:
        sentence = sentence.rstrip(".").strip()
        # "Decreases Health by 10% and when attacking increases damage dealt by 10%" -> two clauses
        parts = re.split(r"\s+and\s+(?=when|for every|increases|decreases)", sentence, flags=re.IGNORECASE)
        clauses.extend(p.strip() for p in parts if p.strip())
    return clauses


def _match_static(clause: str) -> StaticBonus | None:
    for pattern in _PATTERNS:
        match = pattern.match(clause)
        if match:
            stat = _STAT_NAMES[match.group("stat").lower()]
            value = float(match.group("value")) / 100
            sign = match.groupdict().get("sign")
            if sign is not None and sign.lower() == "decreases":
                value = -value
            return StaticBonus(stat=stat, value=value, of_base=stat in _OF_BASE)
    return None
