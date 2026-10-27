"""Hits needed to defeat a hero with one skill: expected damage per hit (M11 part).

Offense = (ATK x rate + HP x hp_scaling + DEF x def_scaling) x 1.871 x pow x (1 + enhancements) (MECH-DMG-01);
defence factor 1 / (1 + DEF x (1 - penetration) / 300) (MECH-DMG-02, MECH-DMG-04); expected hit-type factor
CC x min(CD, 350%) + (1 - CC) (MECH-DMG-05, crushing and miss left out). All `community`. Skill enhancements are
assumed complete (the roster does not know them, NV-32). Speed scaling (Rinak: the offense is multiplied by
1 + Speed x the skill's factor, e7calc `mult`, `assumed`) is counted from the attacker's Speed in its stats. Skill
fields whose formula is not documented ("increased value", extra ATK scaling, crit damage increase) are not counted:
the skill is marked approximate.
Not yet: buffs/debuffs, sets in combat (Torrent, Rage...), artifacts, elements, passives, barriers.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from e7ac.catalog.resolve import ResolvedEntity
from e7ac.domain.codes import Stat

ATK_CONSTANT: Final = 1.871
"""MECH-DMG-01 (community, no official source)."""
CD_CAP: Final = 3.5
"""MECH-DMG-05."""
_UNMODELLED: Final = ("increased_value", "extra_self_atk_scaling", "crit_damage_increase")


@dataclass(frozen=True, slots=True)
class SkillPower:
    rate: float
    pow: float
    enhance: float
    hp_scaling: float = 0.0
    def_scaling: float = 0.0
    spd_scaling: float = 0.0
    """The offense is multiplied by 1 + the caster's Speed x this (Rinak S1 0.0015, S3 0.003; e7calc, `assumed`)."""
    penetration: float = 0.0
    approximate: bool = False
    can_crit: bool = True
    """False when the skill's hit types (Fribbels `hit_types`) leave out "crit" (e.g. a fixed-damage skill)."""
    targets: int = 1
    """Enemies hit (Fribbels `targets`: 2 = two enemies at random, above 2 = all enemies; `assumed`)."""


def skill_power(entity: ResolvedEntity) -> SkillPower | None:
    """None for a skill without damage numbers (buffs, passives)."""
    rate, power = entity.value("rate"), entity.value("pow")
    if not isinstance(rate, (int, float)) or not isinstance(power, (int, float)) or rate <= 0:
        return None
    enhance = entity.value("enhance")
    return SkillPower(
        rate=float(rate),
        pow=float(power),
        enhance=sum(_number(v) for v in enhance) if isinstance(enhance, list) else 0.0,
        hp_scaling=_number(entity.value("self_hp_scaling")),
        def_scaling=_number(entity.value("self_def_scaling")),
        spd_scaling=_number(entity.value("self_spd_scaling")),
        penetration=_number(entity.value("penetration")),
        approximate=any(entity.value(name) is not None for name in _UNMODELLED),
        can_crit=not isinstance(hit_types := entity.value("hit_types"), list) or "crit" in hit_types,
        targets=int(_number(entity.value("targets")) or 1),
    )


def support_amounts(entity: ResolvedEntity) -> dict[str, tuple[float, float, float]]:
    """Heal / barrier amounts of a skill from its Fribbels options ("S2 heal", "S3 barrier"; soulburn ones left out):
    shares of the caster's max Health, Attack and Defense (`community`, MECH-HEAL-01)."""
    found: dict[str, tuple[float, float, float]] = {}
    variants = entity.value("variants")
    for option in variants if isinstance(variants, list) else []:
        if not isinstance(option, dict):
            continue
        name = str(option.get("name", "")).lower()
        kind = next((k for k in ("heal", "barrier") if name.endswith(k) and "soulburn" not in name), None)
        if kind is not None and kind not in found:
            scaling = tuple(_number(option.get(key)) for key in ("selfHpScaling", "selfAtkScaling", "selfDefScaling"))
            if any(scaling):
                found[kind] = (scaling[0], scaling[1], scaling[2])
    return found


def expected_hit(attacker: Mapping[Stat, float], skill: SkillPower, defender: Mapping[Stat, float]) -> float:
    offense = attacker[Stat.ATK] * skill.rate + attacker[Stat.HP] * skill.hp_scaling
    offense += attacker[Stat.DEF] * skill.def_scaling
    offense *= ATK_CONSTANT * skill.pow * (1 + skill.enhance)
    offense *= 1 + attacker.get(Stat.SPEED, 0.0) * skill.spd_scaling
    defence = 1 / (1 + defender[Stat.DEF] * (1 - skill.penetration) / 300)
    crit = min(attacker[Stat.CRIT_CHANCE], 1.0) if skill.can_crit else 0.0
    hit_type = crit * min(attacker[Stat.CRIT_DAMAGE], CD_CAP) + (1 - crit)
    return offense * defence * hit_type


def base_hit(attacker: Mapping[Stat, float], skill: SkillPower, defender: Mapping[Stat, float]) -> float:
    """Damage of a non-critical hit (multiply by min(CD, 350%) for a critical one)."""
    return expected_hit({**attacker, Stat.CRIT_CHANCE: 0.0}, skill, defender)


def hits_to_defeat(attacker: Mapping[Stat, float], skill: SkillPower, defender: Mapping[Stat, float]) -> int:
    return math.ceil(defender[Stat.HP] / expected_hit(attacker, skill, defender))


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
