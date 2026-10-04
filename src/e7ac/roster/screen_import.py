"""Turn a hero-screen reading (OCR) into a roster build, checked against the catalog (SPEC D40).

- The hero is identified by its name: exact catalog match, or a fuzzy match only with a clear margin (reported).
- On the Equipment tab, "final - ▲ bonus" must equal the catalog base stat (Lv60 6★ awakened, MECH-STAT-03): this
  checks the OCR and the catalog at once (MECH-STAT-06).
- Fields the screen does not show are kept from the hero's current build, or assumed and reported (confidence 0).
- The imprint shown is the active one, self or team (MECH-IMP-02). Until the icon is read (M7) the mode is inferred
  from the catalog's self-imprint table (SPEC D42): another stat means team; the own stat with a value of exactly one
  grade means self.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from e7ac.catalog.names import NameIndex
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.domain.codes import Stat
from e7ac.domain.roster import BuildSource, FinalStats, HeroBuild, Imprint, ImprintGrade, ImprintMode
from e7ac.vision.hero_screen import HeroScreenReading, ScreenKind
from e7ac.vision.labels import match_label

FINAL_FIELDS: Final[Mapping[Stat, str]] = {
    Stat.ATK: "atk",
    Stat.DEF: "defense",
    Stat.HP: "hp",
    Stat.SPEED: "speed",
    Stat.CRIT_CHANCE: "crit_chance",
    Stat.CRIT_DAMAGE: "crit_damage",
    Stat.EFFECTIVENESS: "effectiveness",
    Stat.EFFECT_RESISTANCE: "effect_resistance",
    Stat.DUAL_ATTACK: "dual_attack",
}
BASE_FIELDS: Final[Mapping[Stat, str]] = {stat: f"base.{stat.value}" for stat in FINAL_FIELDS}
_FLAT: Final = frozenset({Stat.ATK, Stat.DEF, Stat.HP, Stat.SPEED})
ASSUMED: Final = 0.0
"""Confidence of a field the screen does not show and that had no previous value (a placeholder to correct)."""
INFERRED_MODE: Final = 0.8
"""Confidence of an imprint mode inferred from the catalog (the icon, read in M7, is the direct evidence)."""


@dataclass(frozen=True, slots=True)
class BaseCheck:
    stat: Stat
    observed: float
    """final - ▲ bonus, read on the screen."""
    expected: float
    """Catalog base stat."""

    @property
    def ok(self) -> bool:
        tolerance = 0.5 if self.stat in _FLAT else 0.0005
        return abs(self.observed - self.expected) <= tolerance


@dataclass(slots=True)
class ScreenBuild:
    hero_code: str | None = None
    hero_name: str | None = None
    build: HeroBuild | None = None
    base_checks: list[BaseCheck] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    """Blocking: no build was made."""
    notes: list[str] = field(default_factory=list)
    """Not blocking: assumptions and checks to look at."""


def build_from_screen(
    reading: HeroScreenReading,
    heroes: Mapping[str, ResolvedEntity],
    *,
    captured_at: datetime,
    existing: HeroBuild | None = None,
) -> ScreenBuild:
    """`heroes`: catalog hero entities by code. `existing`: the hero's current build (fields the screen lacks)."""
    result = ScreenBuild(hero_name=reading.name)
    result.notes.extend(f"screen: {w}" for w in reading.warnings)
    code = _hero_code(reading, heroes, result)
    if code is None:
        return result
    result.hero_code = code
    if existing is not None and existing.hero_code != code:
        result.problems.append(f"the screen shows {code}, but the chosen roster hero is {existing.hero_code}")
        return result
    hero = heroes[code]
    stats = _final_stats(reading, result)
    if stats is None:
        return result
    confidence: dict[str, float] = {}
    for stat, stat_reading in reading.stats.items():
        confidence[f"final_stats.{FINAL_FIELDS[stat]}"] = stat_reading.confidence
    if reading.kind is ScreenKind.EQUIPMENT:
        _base_checks(reading, hero, result, confidence)
    else:
        result.notes.append("Hero Info screen: no '▲' bonus shown, so the base-stat check is not possible")
    data: dict[str, Any] = existing.model_dump() if existing is not None else {"hero_code": code}
    data.update(
        final_stats=stats,
        captured_at=captured_at,
        source=BuildSource.OCR,
        cp=reading.cp if reading.cp is not None else data.get("cp"),
        confidence={**_kept_confidence(existing), **confidence},
    )
    _level_and_stars(reading, existing, data, result)
    _imprint(reading, hero, existing, data, result)
    result.build = HeroBuild.model_validate(data)
    return result


def _hero_code(reading: HeroScreenReading, heroes: Mapping[str, ResolvedEntity], result: ScreenBuild) -> str | None:
    if not reading.name:
        result.problems.append("the hero name was not read")
        return None
    index = NameIndex()
    for hero_code, entity in heroes.items():
        index.add(entity.name, hero_code)
    if index.is_ambiguous(reading.name):
        result.problems.append(f"several catalog heroes are called {reading.name!r}: use --id to choose the hero")
        return None
    found = index.lookup(reading.name)
    if found is not None:
        return found
    names = {entity.name: c for c, entity in heroes.items() if not index.is_ambiguous(entity.name)}
    near = match_label(reading.name, names)
    if near is None:
        result.problems.append(f"no catalog hero is called {reading.name!r} (OCR error, or run: e7 catalog sync)")
        return None
    result.notes.append(f"name read as {reading.name!r}, matched to {near!r} (clear best match)")
    return names[near]


def _final_stats(reading: HeroScreenReading, result: ScreenBuild) -> FinalStats | None:
    values: dict[str, Any] = {}
    for stat, name in FINAL_FIELDS.items():
        stat_reading = reading.stats.get(stat)
        if stat_reading is None or stat_reading.final is None:
            raw = stat_reading.raw if stat_reading is not None else "row not found"
            result.problems.append(f"{stat.value}: value not readable ({raw!r})")
            continue
        values[name] = int(stat_reading.final) if stat in _FLAT else stat_reading.final
    if result.problems:
        return None
    return FinalStats.model_validate(values)


def _base_checks(
    reading: HeroScreenReading, hero: ResolvedEntity, result: ScreenBuild, confidence: dict[str, float]
) -> None:
    for stat, stat_reading in reading.stats.items():
        expected = hero.value(BASE_FIELDS[stat])
        if stat_reading.final is None or not isinstance(expected, (int, float)) or isinstance(expected, bool):
            continue
        check = BaseCheck(stat, round(stat_reading.final - (stat_reading.bonus or 0.0), 6), float(expected))
        result.base_checks.append(check)
        key = f"final_stats.{FINAL_FIELDS[stat]}"
        if check.ok:
            confidence[key] = 1.0  # two independent sources agree
        else:
            confidence[key] = min(confidence.get(key, 1.0), 0.5)
            result.notes.append(
                f"{stat.value}: the screen implies base {check.observed:g} but the catalog says {check.expected:g} "
                "(OCR error, or the catalog is out of date)"
            )


def _level_and_stars(
    reading: HeroScreenReading, existing: HeroBuild | None, data: dict[str, Any], result: ScreenBuild
) -> None:
    confidence: dict[str, float] = data["confidence"]
    if reading.level is not None and reading.level_cap is not None:
        data["level"] = reading.level
        confidence["level"] = 1.0
        stars = reading.level_cap // 10  # MECH-HERO-01: level cap = stars x 10 (community)
        if existing is not None and existing.stars != stars:
            result.notes.append(
                f"stars: the level cap {reading.level_cap} implies {stars}*, the roster had {existing.stars}*"
            )
        data["stars"] = stars
        confidence["stars"] = 0.9
    elif existing is None:
        data["level"], data["stars"] = 60, 6
        confidence["level"] = confidence["stars"] = ASSUMED
        result.notes.append("level not read: assumed 6* Lv. 60 (correct it with e7 roster edit)")
    if existing is None:
        data["awakening"] = data["stars"]
        confidence["awakening"] = ASSUMED
        result.notes.append(
            f"awakening is not shown on this screen: assumed {data['stars']} (e7 roster edit --awakening N to fix)"
        )
    elif data.get("awakening", 0) > data["stars"]:
        data["awakening"] = data["stars"]


def _imprint(
    reading: HeroScreenReading,
    hero: ResolvedEntity,
    existing: HeroBuild | None,
    data: dict[str, Any],
    result: ScreenBuild,
) -> None:
    confidence: dict[str, float] = data["confidence"]
    had = existing.imprint if existing is not None else None
    if reading.imprint_locked:
        if had is not None:
            result.notes.append(f"imprint: the screen shows 'Locked' (none); the roster had {_describe(had)}")
        data["imprint"] = None
        confidence["imprint"] = 1.0
        return
    if reading.imprint_stat is None or reading.imprint_value is None:
        if had is not None:
            result.notes.append(f"imprint not read: kept from the current build ({_describe(had)})")
        else:
            data["imprint"] = None
            confidence["imprint"] = ASSUMED
            result.notes.append("imprint not read: stored as none (check it on the screen)")
        return
    shown = f"imprint {reading.imprint_raw!r}"
    own_stat = hero.value("imprint.stat")
    grades = hero.value("imprint.values")
    mode: ImprintMode | None = None
    grade: ImprintGrade | None = None
    if not isinstance(own_stat, str):
        result.notes.append(f"{shown}: the catalog has no imprint table for this hero, so self/team is unknown")
    elif own_stat != reading.imprint_stat.value:
        mode = ImprintMode.TEAM  # a self imprint always gives the hero's own imprint stat
        result.notes.append(
            f"{shown}: not this hero's own imprint ({own_stat}), so it is the team imprint (MECH-IMP-02); "
            "its grade is shown as an icon only"
        )
    else:
        value = reading.imprint_value
        matches = [
            g
            for g, v in (grades.items() if isinstance(grades, dict) else ())
            if isinstance(v, (int, float)) and abs(v - value) < 1e-6
        ]
        if len(matches) == 1 and matches[0] in ImprintGrade._value2member_map_:
            mode, grade = ImprintMode.SELF, ImprintGrade(matches[0])
        else:
            result.notes.append(
                f"{shown}: this hero's own imprint stat, but the value is not exactly one grade of the catalog "
                "table, so self/team and the grade are unknown"
            )
    data["imprint"] = Imprint(grade=grade, stat=reading.imprint_stat, value=reading.imprint_value, mode=mode)
    confidence["imprint"] = 1.0
    confidence["imprint.mode"] = INFERRED_MODE if mode is not None else ASSUMED
    confidence["imprint.grade"] = INFERRED_MODE if grade is not None else ASSUMED


def _describe(imprint: Imprint) -> str:
    grade = imprint.grade.value if imprint.grade is not None else "?"
    mode = f", {imprint.mode.value}" if imprint.mode is not None else ""
    return f"{imprint.stat.value} {imprint.value:g} (grade {grade}{mode})"


def _kept_confidence(existing: HeroBuild | None) -> dict[str, float]:
    return dict(existing.confidence) if existing is not None else {}
