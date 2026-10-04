"""Validation of hero builds. Mismatches are reported, never silently fixed (golden rule).

Severity policy (SPEC D31):
- ERROR only for violations of our own data contract, which make a value meaningless whatever the game rules are:
  units (a gear/imprint/EE rate above 100%, a final rate above 1000%, a flat gear stat below 1) and non-positive
  gear values. Errors block a save unless the user forces it.
- WARNING for every game rule that is not `verified` in docs/MECHANICS.md (today: all of them - MECH-GEAR-03/08/09/10,
  MECH-HERO-01/02, MECH-ART-04, MECH-IMP-01, MECH-EE-01, MECH-STAT-03/05) and for codes the catalog does not know.
  A wrong rule of ours can therefore never reject real data; warnings are always printed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Container, Mapping
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
# Unit sanity limits (data contract, not game numbers): one gear piece, imprint or EE never gives 100% or more of a
# rate (the largest verified gear main is 70% CD, MECH-GEAR-02); a final rate of 1000% means percent was stored as
# a fraction twice. Flat stats are whole game units.
MAX_COMPONENT_RATE: Final = 1.0
MAX_FINAL_RATE: Final = 10.0
MIN_FLAT_VALUE: Final = 1.0
_FINAL_RATES: Final = ("crit_chance", "crit_damage", "effectiveness", "effect_resistance", "dual_attack")


@dataclass(frozen=True, slots=True)
class CatalogContext:
    """Catalog facts for the catalog-aware checks. `None` = unknown (check skipped); statuses go into messages."""

    known_heroes: Container[str] | None = None
    known_artifacts: Container[str] | None = None
    known_sets: Container[str] | None = None
    hero_role: str | None = None
    artifact_role_lock: str | None = None
    artifact_role_lock_status: str | None = None
    imprint_stat: Stat | None = None
    imprint_values: Mapping[str, float] | None = None
    ee_stat: Stat | None = None
    base_crit_damage: float | None = None


def validate_build(build: HeroBuild, catalog: CatalogContext | None = None) -> list[Issue]:
    """Check a build. Catalog facts enable extra checks when given (MECH-ART-04, MECH-IMP-01, MECH-EE-01...)."""
    ctx = catalog or CatalogContext()
    issues: list[Issue] = []
    for slot, gear in build.gear.items():
        issues.extend(validate_gear(gear, field=f"gear.{slot.value}"))
    issues.extend(_catalog_codes(build, ctx))

    if build.level > build.stars * 10:
        issues.append(
            Issue(Severity.WARNING, "MECH-HERO-01", "level", f"level {build.level} exceeds {build.stars}-star cap")
        )
    if build.awakening > build.stars:
        issues.append(Issue(Severity.WARNING, "MECH-HERO-02", "awakening", "awakening above star count is unusual"))
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
        for name in _FINAL_RATES:
            value = getattr(stats, name)
            if value > MAX_FINAL_RATE:
                issues.append(_unit_rate(f"final_stats.{name}", value))
        if ctx.base_crit_damage is not None and stats.crit_damage < ctx.base_crit_damage:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "MECH-STAT-03",
                    "final_stats.crit_damage",
                    f"crit damage {stats.crit_damage:.0%} is below this hero's base {ctx.base_crit_damage:.0%} "
                    "(catalog) - entered as a fraction instead of percent?",
                )
            )
    if (
        build.artifact is not None
        and ctx.hero_role
        and ctx.artifact_role_lock
        and ctx.artifact_role_lock != ctx.hero_role
    ):
        status = f" (catalog class lock: {ctx.artifact_role_lock_status})" if ctx.artifact_role_lock_status else ""
        issues.append(
            Issue(
                Severity.WARNING,
                "MECH-ART-04",
                "artifact",
                f"artifact {build.artifact.code} is {ctx.artifact_role_lock}-exclusive but the hero is "
                f"{ctx.hero_role}{status}",
            )
        )
    if build.imprint is not None:
        if build.imprint.stat.is_rate and build.imprint.value > MAX_COMPONENT_RATE:
            issues.append(_unit_rate("imprint.value", build.imprint.value))
        imprint_stat, imprint_values = ctx.imprint_stat, ctx.imprint_values
        if imprint_stat is not None and build.imprint.stat is not imprint_stat:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "MECH-IMP-01",
                    "imprint.stat",
                    f"catalog says this hero's imprint is {imprint_stat.value}, not {build.imprint.stat.value}",
                )
            )
        grade = build.imprint.grade
        expected = (imprint_values or {}).get(grade.value) if grade is not None else None
        if expected is not None and abs(expected - build.imprint.value) > 1e-6:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "MECH-IMP-01",
                    "imprint.value",
                    f"catalog says grade {grade.value if grade else '?'} gives {expected:g}, not "
                    f"{build.imprint.value:g}",
                )
            )
    ee = build.exclusive_equipment
    if ee is not None and ee.stat is not None and ee.stat.is_rate and (ee.value or 0) > MAX_COMPONENT_RATE:
        issues.append(_unit_rate("exclusive_equipment.value", ee.value or 0))
    if ee is not None and ee.stat is not None and ctx.ee_stat is not None and ee.stat is not ctx.ee_stat:
        issues.append(
            Issue(
                Severity.WARNING,
                "MECH-EE-01",
                "exclusive_equipment.stat",
                f"catalog says this hero's EE stat is {ctx.ee_stat.value}, not {ee.stat.value}",
            )
        )
    return issues


def _catalog_codes(build: HeroBuild, ctx: CatalogContext) -> list[Issue]:
    """Codes the synced catalog does not know (typo, or a hero newer than the catalog): warn, never refuse."""
    issues = []
    if ctx.known_heroes is not None and build.hero_code not in ctx.known_heroes:
        issues.append(_unknown("hero_code", build.hero_code, "hero"))
    if ctx.known_artifacts is not None and build.artifact and build.artifact.code not in ctx.known_artifacts:
        issues.append(_unknown("artifact", build.artifact.code, "artifact"))
    if ctx.known_sets is not None:
        for slot, gear in sorted(build.gear.items()):
            if gear.set_code not in ctx.known_sets:
                issues.append(_unknown(f"gear.{slot.value}.set_code", gear.set_code, "set"))
    return issues


def _unknown(field: str, code: str, kind: str) -> Issue:
    return Issue(
        Severity.WARNING,
        "CATALOG-UNKNOWN",
        field,
        f"{kind} code {code!r} is not in the catalog (typo, or newer than the last sync); catalog checks skipped",
    )


def _unit_rate(field: str, value: float) -> Issue:
    return Issue(
        Severity.ERROR,
        "UNIT-RATE",
        field,
        f"{value * 100:g}% is not a plausible rate here - percent and fraction mixed up? "
        "(stored as fractions: 0.12 = 12%; e7 command options take percent: --cc 12)",
    )


def validate_gear(gear: Gear, *, field: str = "gear") -> list[Issue]:
    issues: list[Issue] = []
    main = gear.main.stat
    if main not in GEAR_STATS:
        issues.append(Issue(Severity.WARNING, "MECH-GEAR-08", f"{field}.main", f"{main.value} is not a gear stat"))
    elif main not in ALLOWED_MAIN[gear.slot]:
        issues.append(
            Issue(
                Severity.WARNING,
                "MECH-GEAR-08",
                f"{field}.main",
                f"{gear.slot.value} is not expected to have main stat {main.value}",
            )
        )
    issues.extend(_value_issues(main, gear.main.value, f"{field}.main"))

    if len(gear.substats) > MAX_SUBSTATS:
        issues.append(
            Issue(Severity.WARNING, "MECH-GEAR-03", f"{field}.substats", f"{len(gear.substats)} substats (max 4)")
        )
    counts = Counter(sub.stat for sub in gear.substats)
    for stat, count in counts.items():
        if count > 1:
            issues.append(
                Issue(Severity.WARNING, "MECH-GEAR-03", f"{field}.substats", f"{stat.value} appears {count} times")
            )
    for index, sub in enumerate(gear.substats):
        where = f"{field}.substats[{index}]"
        if sub.stat not in GEAR_STATS:
            issues.append(Issue(Severity.WARNING, "MECH-GEAR-08", where, f"{sub.stat.value} is not a gear stat"))
        if sub.stat is main:
            issues.append(Issue(Severity.WARNING, "MECH-GEAR-03", where, "a substat cannot repeat the main stat"))
        if sub.stat in FORBIDDEN_SUBS.get(gear.slot, frozenset()):
            issues.append(
                Issue(
                    Severity.WARNING,
                    "MECH-GEAR-09",
                    where,
                    f"{gear.slot.value} is not expected to roll {sub.stat.value}",
                )
            )
        issues.extend(_value_issues(sub.stat, sub.value, where))

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


def _value_issues(stat: Stat, value: float, field: str) -> list[Issue]:
    """Unit sanity of one gear value (data contract -> errors)."""
    if value <= 0:
        return [Issue(Severity.ERROR, "GEAR-VALUE", field, "value must be positive")]
    if stat.is_rate and value > MAX_COMPONENT_RATE:
        return [_unit_rate(field, value)]
    if not stat.is_rate and value < MIN_FLAT_VALUE:
        return [
            Issue(
                Severity.ERROR,
                "UNIT-FLAT",
                field,
                f"flat {stat.value} {value:g} is below 1 - a percentage stored as a flat stat?",
            )
        ]
    return []


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
