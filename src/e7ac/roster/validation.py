"""Validation of hero builds. Mismatches are reported, never silently fixed (golden rule).

Severity policy: ERROR only for game invariants we are sure of (they make a build impossible); rules that come
from community sources only are WARNINGs, so a wrong rule of ours can never reject real data.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from e7ac.domain.codes import Stat
from e7ac.domain.roster import Gear, GearGrade, GearSlot, HeroBuild


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Issue:
    severity: Severity
    rule: str
    field: str
    message: str


GEAR_STATS: Final = frozenset(
    {
        Stat.ATK,
        Stat.ATK_PERCENT,
        Stat.HP,
        Stat.HP_PERCENT,
        Stat.DEF,
        Stat.DEF_PERCENT,
        Stat.SPEED,
        Stat.CRIT_CHANCE,
        Stat.CRIT_DAMAGE,
        Stat.EFFECTIVENESS,
        Stat.EFFECT_RESISTANCE,
    }
)
_FLEX: Final = frozenset({Stat.ATK_PERCENT, Stat.HP_PERCENT, Stat.DEF_PERCENT, Stat.ATK, Stat.HP, Stat.DEF})
# MECH-GEAR-01 / MECH-GEAR-08
ALLOWED_MAIN: Final[Mapping[GearSlot, frozenset[Stat]]] = {
    GearSlot.WEAPON: frozenset({Stat.ATK}),
    GearSlot.HELMET: frozenset({Stat.HP}),
    GearSlot.ARMOR: frozenset({Stat.DEF}),
    GearSlot.NECKLACE: _FLEX | {Stat.CRIT_CHANCE, Stat.CRIT_DAMAGE},
    GearSlot.RING: _FLEX | {Stat.EFFECTIVENESS, Stat.EFFECT_RESISTANCE},
    GearSlot.BOOTS: _FLEX | {Stat.SPEED},
}
# MECH-GEAR-09
FORBIDDEN_SUBS: Final[Mapping[GearSlot, frozenset[Stat]]] = {
    GearSlot.WEAPON: frozenset({Stat.DEF, Stat.DEF_PERCENT}),
    GearSlot.ARMOR: frozenset({Stat.ATK, Stat.ATK_PERCENT}),
}
# MECH-GEAR-10: (minimum enhancement, minimum substat count)
MIN_SUBS_BY_ENHANCE: Final = ((12, 4), (9, 3), (6, 2), (3, 1))
MAX_SUBSTATS: Final = 4


def validate_build(
    build: HeroBuild,
    *,
    hero_role: str | None = None,
    artifact_role_lock: str | None = None,
    imprint_stat: Stat | None = None,
    imprint_values: Mapping[str, float] | None = None,
    ee_stat: Stat | None = None,
) -> list[Issue]:
    """Check a build. Catalog facts (role, artifact class lock, imprint/EE stat) enable extra checks when given."""
    issues: list[Issue] = []
    for slot, gear in build.gear.items():
        issues.extend(validate_gear(gear, field=f"gear.{slot.value}"))

    if build.level > build.stars * 10:
        issues.append(
            Issue(Severity.WARNING, "MECH-HERO-01", "level", f"level {build.level} exceeds {build.stars}-star cap")
        )
    if build.awakening > build.stars:
        issues.append(Issue(Severity.WARNING, "HERO-AWAKENING", "awakening", "awakening above star count is unusual"))
    stats = build.final_stats
    if stats is not None:
        if stats.crit_chance > 1.0:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "MECH-STAT-05",
                    "final_stats.crit_chance",
                    f"crit chance {stats.crit_chance:.0%} above the 100% the game displays (read error?)",
                )
            )
        for name in ("crit_chance", "crit_damage", "effectiveness", "effect_resistance", "dual_attack"):
            value = getattr(stats, name)
            if value > 10:
                issues.append(
                    Issue(
                        Severity.ERROR,
                        "UNIT-RATE",
                        f"final_stats.{name}",
                        f"{value} looks like a percentage; rates are fractions (1.0 = 100%)",
                    )
                )
    if build.artifact is not None and hero_role and artifact_role_lock and artifact_role_lock != hero_role:
        issues.append(
            Issue(
                Severity.ERROR,
                "ARTIFACT-CLASS-LOCK",
                "artifact",
                f"artifact {build.artifact.code} is {artifact_role_lock}-exclusive but the hero is {hero_role}",
            )
        )
    if build.imprint is not None:
        if imprint_stat is not None and build.imprint.stat is not imprint_stat:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "MECH-IMP-01",
                    "imprint.stat",
                    f"catalog says this hero's imprint is {imprint_stat.value}, not {build.imprint.stat.value}",
                )
            )
        expected = (imprint_values or {}).get(build.imprint.grade.value)
        if expected is not None and abs(expected - build.imprint.value) > 1e-6:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "MECH-IMP-01",
                    "imprint.value",
                    f"catalog says grade {build.imprint.grade.value} gives {expected:g}, not {build.imprint.value:g}",
                )
            )
    ee = build.exclusive_equipment
    if ee is not None and ee.stat is not None and ee_stat is not None and ee.stat is not ee_stat:
        issues.append(
            Issue(
                Severity.WARNING,
                "MECH-EE-01",
                "exclusive_equipment.stat",
                f"catalog says this hero's EE stat is {ee_stat.value}, not {ee.stat.value}",
            )
        )
    return issues


def validate_gear(gear: Gear, *, field: str = "gear") -> list[Issue]:
    issues: list[Issue] = []
    main = gear.main.stat
    if main not in GEAR_STATS:
        issues.append(Issue(Severity.ERROR, "GEAR-STAT", f"{field}.main", f"{main.value} is not a gear stat"))
    elif main not in ALLOWED_MAIN[gear.slot]:
        issues.append(
            Issue(
                Severity.ERROR,
                "MECH-GEAR-08",
                f"{field}.main",
                f"{gear.slot.value} cannot have main stat {main.value}",
            )
        )
    if gear.main.value <= 0:
        issues.append(Issue(Severity.ERROR, "GEAR-VALUE", f"{field}.main", "main stat value must be positive"))
    if main.is_rate and gear.main.value > 10:
        issues.append(
            Issue(Severity.ERROR, "UNIT-RATE", f"{field}.main", "looks like a percentage; rates are fractions")
        )

    if len(gear.substats) > MAX_SUBSTATS:
        issues.append(
            Issue(Severity.ERROR, "MECH-GEAR-03", f"{field}.substats", f"{len(gear.substats)} substats (max 4)")
        )
    counts = Counter(sub.stat for sub in gear.substats)
    for stat, count in counts.items():
        if count > 1:
            issues.append(
                Issue(Severity.ERROR, "MECH-GEAR-03", f"{field}.substats", f"{stat.value} appears {count} times")
            )
    for index, sub in enumerate(gear.substats):
        where = f"{field}.substats[{index}]"
        if sub.stat not in GEAR_STATS:
            issues.append(Issue(Severity.ERROR, "GEAR-STAT", where, f"{sub.stat.value} is not a gear stat"))
        if sub.stat is main:
            issues.append(Issue(Severity.ERROR, "MECH-GEAR-03", where, "a substat cannot repeat the main stat"))
        if sub.stat in FORBIDDEN_SUBS.get(gear.slot, frozenset()):
            issues.append(
                Issue(
                    Severity.WARNING,
                    "MECH-GEAR-09",
                    where,
                    f"{gear.slot.value} is not expected to roll {sub.stat.value}",
                )
            )
        if sub.value <= 0:
            issues.append(Issue(Severity.ERROR, "GEAR-VALUE", where, "substat value must be positive"))
        if sub.stat.is_rate and sub.value > 10:
            issues.append(Issue(Severity.ERROR, "UNIT-RATE", where, "looks like a percentage; rates are fractions"))

    for min_enhance, min_subs in MIN_SUBS_BY_ENHANCE:
        if gear.enhance >= min_enhance:
            if len(gear.substats) < min_subs:
                issues.append(
                    Issue(
                        Severity.WARNING,
                        "MECH-GEAR-10",
                        f"{field}.substats",
                        f"+{gear.enhance} gear usually has at least {min_subs} substats, found {len(gear.substats)}",
                    )
                )
            break
    else:
        if gear.grade is not GearGrade.NORMAL and not gear.substats:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "MECH-GEAR-10",
                    f"{field}.substats",
                    f"{gear.grade.value} gear usually starts with at least 1 substat",
                )
            )
    return issues


def active_sets(build: HeroBuild, pieces_required: Mapping[str, int]) -> dict[str, int]:
    """Completed set bonuses: set code -> number of times its bonus applies (e.g. 3x Health 2-piece = 3).

    Sets whose piece count is unknown are left out (the caller should report them)."""
    counts = Counter(gear.set_code for gear in build.gear.values())
    active: dict[str, int] = {}
    for set_code, count in counts.items():
        required = pieces_required.get(set_code)
        if required:
            times = count // required
            if times:
                active[set_code] = times
    return active
