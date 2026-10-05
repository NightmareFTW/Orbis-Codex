"""Turn a hero-screen reading (OCR) into a roster build, checked against the catalog (SPEC D40).

- The hero is identified by its name: exact catalog match, or a fuzzy match only with a clear margin (reported).
- On the Equipment tab, "final - ▲ bonus" must equal the catalog base stat (Lv60 6★ awakened, MECH-STAT-03): this
  checks the OCR and the catalog at once (MECH-STAT-06).
- Fields the screen does not show are kept from the hero's current build, or assumed and reported (confidence 0).
- The imprint shown is the active one, self or team (MECH-IMP-02): inferred from the catalog's self-imprint table
  (SPEC D42: another stat means team; the own stat with a value of exactly one grade means self), and read from the
  icon on a Hero Info capture (M7), which wins.
- A Hero Info capture also gives the gear, sets, artifact, EE and awakening (`screen_gear`, SPEC D51), and the
  displayed stats are then recomposed from them as an independent check (`composition`, MECH-STAT-02/08).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from e7ac.catalog.names import NameIndex, normalise_name
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.domain.codes import Stat
from e7ac.domain.roster import BuildSource, FinalStats, HeroBuild, Imprint, ImprintGrade, ImprintMode
from e7ac.roster.composition import CompositionReport, check_final_stats
from e7ac.roster.screen_gear import ScreenCatalog, apply_images, apply_imprint_icon, apply_stars, set_consistency
from e7ac.vision.hero_screen import HeroScreenReading, ScreenKind
from e7ac.vision.labels import match_label

if TYPE_CHECKING:  # imports OpenCV: loaded only when a capture is read
    from e7ac.vision.hero_info import HeroImageReading

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
_TWINS: Final[Mapping[Stat, str]] = {
    Stat.ATK: Stat.ATK_PERCENT.value,
    Stat.ATK_PERCENT: Stat.ATK.value,
    Stat.DEF: Stat.DEF_PERCENT.value,
    Stat.DEF_PERCENT: Stat.DEF.value,
    Stat.HP: Stat.HP_PERCENT.value,
    Stat.HP_PERCENT: Stat.HP.value,
}
"""Flat and percent versions of a stat: a lost '%' turns one into the other."""
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
    composition: CompositionReport | None = None
    """The final-stat check of a Hero Info capture whose gear was read (None when it did not run)."""


def build_from_screen(
    reading: HeroScreenReading,
    heroes: Mapping[str, ResolvedEntity],
    *,
    captured_at: datetime,
    existing: HeroBuild | None = None,
    images: HeroImageReading | None = None,
    catalog: ScreenCatalog | None = None,
) -> ScreenBuild:
    """`heroes`: catalog hero entities by code. `existing`: the hero's current build (fields the screen lacks).
    `images`: what the image readers found on a Hero Info capture (gear, icons, stars); `catalog`: the artifact and
    set entities for the final-stat check."""
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
    elif images is None or not images.panel.present:
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
    if images is not None:
        apply_stars(images, existing, data, result.notes)
    _imprint(reading, hero, existing, data, result)
    if images is not None:
        apply_imprint_icon(images.imprint_icon, reading.imprint_locked, data, result.notes)
        apply_images(images, existing, data, result.notes)
    try:
        build = HeroBuild.model_validate(data)
    except ValidationError as exc:
        result.problems.append(f"the screen values do not make a valid build: {exc.errors()[0]['msg']}")
        return result
    result.build = build
    if images is not None and catalog is not None and images.panel.present:
        result.notes.extend(set_consistency(build, images, catalog.sets))
        artifact = catalog.artifacts.get(build.artifact.code) if build.artifact is not None else None
        report = check_final_stats(build, hero, artifact=artifact, sets=catalog.sets)
        result.composition = report
        if report.applicable:
            result.notes.extend(f"stat check: {w}" for w in report.warnings)
        else:
            result.notes.append(f"stat check not run: {report.reason}")
    return result


def resolve_hero_code(name: str | None, heroes: Mapping[str, ResolvedEntity]) -> tuple[str | None, str]:
    """(catalog code, "") for the name read on a screen, or (None, problem). The code comes from an exact catalog
    name, else from the margin rule (`match_label`), never a near-miss; a name shared by several heroes is refused."""
    if not name:
        return None, "the hero name was not read"
    index = NameIndex()
    for hero_code, entity in heroes.items():
        index.add(entity.name, hero_code)
    if index.is_ambiguous(name):
        return None, f"several catalog heroes are called {name!r}: use --id to choose the hero"
    found = index.lookup(name)
    if found is not None:
        return found, ""
    names = {entity.name: c for c, entity in heroes.items() if not index.is_ambiguous(entity.name)}
    near = match_label(name, names)
    if near is None:
        return None, f"no catalog hero is called {name!r} (OCR error, or run: e7 catalog sync)"
    return names[near], ""


def _hero_code(reading: HeroScreenReading, heroes: Mapping[str, ResolvedEntity], result: ScreenBuild) -> str | None:
    code, problem = resolve_hero_code(reading.name, heroes)
    if code is None:
        result.problems.append(problem)
        return None
    if reading.name is not None and normalise_name(heroes[code].name) != normalise_name(reading.name):
        result.notes.append(f"name read as {reading.name!r}, matched to {heroes[code].name!r} (clear best match)")
    return code


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
        _no_imprint(data, confidence, 1.0)
        return
    if reading.imprint_stat is None or reading.imprint_value is None:
        if had is not None:
            result.notes.append(f"imprint not read: kept from the current build ({_describe(had)})")
        else:
            _no_imprint(data, confidence, ASSUMED)
            result.notes.append("imprint not read: stored as none (check it on the screen)")
        return
    mode, grade = _infer_mode(reading, hero, result)
    shown_stat, shown_value = reading.imprint_stat, reading.imprint_value
    confidence["imprint"] = 1.0
    confidence["imprint.mode"] = INFERRED_MODE if mode is not None else ASSUMED
    confidence["imprint.grade"] = INFERRED_MODE if grade is not None else ASSUMED
    if had is not None and existing is not None and had.stat is shown_stat and abs(had.value - shown_value) < 1e-9:
        # same imprint as the roster: what the screen cannot tell (yet) is kept, and a disagreement is reported
        mode = _keep_known("mode", mode, had.mode, existing.confidence, confidence, result)
        grade = _keep_known("grade", grade, had.grade, existing.confidence, confidence, result)
    data["imprint"] = Imprint(grade=grade, stat=shown_stat, value=shown_value, mode=mode)


def _infer_mode(
    reading: HeroScreenReading, hero: ResolvedEntity, result: ScreenBuild
) -> tuple[ImprintMode | None, ImprintGrade | None]:
    """Self/team and grade from the catalog's self-imprint table (SPEC D42); None when the evidence is not clear."""
    assert reading.imprint_stat is not None and reading.imprint_value is not None
    shown = f"imprint {reading.imprint_raw!r}"
    own_stat = hero.value("imprint.stat")
    if not isinstance(own_stat, str):
        result.notes.append(f"{shown}: the catalog has no imprint table for this hero, so self/team is unknown")
        return None, None
    if own_stat != reading.imprint_stat.value:
        if _TWINS.get(reading.imprint_stat) == own_stat:
            result.notes.append(
                f"{shown}: the flat/percent twin of this hero's own imprint ({own_stat}); a misread '%' or a team "
                "imprint - self/team unknown (check the icon)"
            )
            return None, None
        result.notes.append(
            f"{shown}: not this hero's own imprint ({own_stat}), so it is the team imprint (MECH-IMP-02); "
            "its grade is shown as an icon only"
        )
        return ImprintMode.TEAM, None  # a self imprint always gives the hero's own imprint stat
    grades = hero.value("imprint.values")
    value = reading.imprint_value
    matches = [
        g
        for g, v in (grades.items() if isinstance(grades, dict) else ())
        if isinstance(v, (int, float)) and abs(v - value) < 1e-6
    ]
    if len(matches) == 1 and matches[0] in ImprintGrade._value2member_map_:
        return ImprintMode.SELF, ImprintGrade(matches[0])
    result.notes.append(
        f"{shown}: this hero's own imprint stat, but the value is not exactly one grade of the catalog table, "
        "so self/team and the grade are unknown"
    )
    return None, None


def _keep_known[T: (ImprintMode, ImprintGrade)](
    name: str,
    inferred: T | None,
    known: T | None,
    known_confidence: Mapping[str, float],
    confidence: dict[str, float],
    result: ScreenBuild,
) -> T | None:
    key = f"imprint.{name}"
    if known is None or inferred == known:
        if known is not None:
            confidence[key] = max(confidence[key], known_confidence.get(key, 1.0))
        return inferred
    old = known_confidence.get(key, 1.0)
    if inferred is None:
        confidence[key] = old
        return known
    if old > confidence[key]:
        result.notes.append(
            f"imprint {name}: the catalog suggests {inferred.value}, the roster has {known.value} (kept: more certain)"
        )
        confidence[key] = old
        return known
    result.notes.append(f"imprint {name}: the roster had {known.value}, the catalog suggests {inferred.value} (taken)")
    return inferred


def _no_imprint(data: dict[str, Any], confidence: dict[str, float], certainty: float) -> None:
    data["imprint"] = None
    confidence["imprint"] = certainty
    confidence.pop("imprint.mode", None)
    confidence.pop("imprint.grade", None)


def _describe(imprint: Imprint) -> str:
    grade = imprint.grade.value if imprint.grade is not None else "?"
    mode = f", {imprint.mode.value}" if imprint.mode is not None else ""
    return f"{imprint.stat.value} {imprint.value:g} (grade {grade}{mode})"


def _kept_confidence(existing: HeroBuild | None) -> dict[str, float]:
    return dict(existing.confidence) if existing is not None else {}
