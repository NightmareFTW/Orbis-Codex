"""Final-stat composition check: do a build's components reproduce the final stats shown on the hero screens?

The displayed stats are the source of truth (MECH-STAT-01). This module recomposes them from the catalog base and the
build's components, and reports every stat that does not fit. It is an independent cross-check of the gear,
set, artifact, imprint and EE readings (M7). A mismatch is a warning that names its suspects. Nothing is ever
corrected, and when a component is unknown the affected stat is "unknown", never a mismatch.

Model:
- MECH-STAT-02 (`community`): ATK/DEF/HP = base · (1 + Σ% of base) + Σflat. The % terms come from gear %, a self
  imprint % and static set bonuses; the flat terms from gear, the artifact and flat imprints. Speed = base ·
  (1 + Σ speed-set %) + Σflat (MECH-STAT-07). Rates (CC, CD, EFF, ER, DAC) are additive. The "final multipliers"
  of MECH-STAT-02 (e.g. permanent passives) are not modelled; none was needed on the captures.
- MECH-STAT-03: the base is the catalog Lv60 6-star fully awakened base, so only such a build is checked.
- Static sets: catalog `pieces` (MECH-GEAR-05) and `static_bonus` (MECH-GEAR-06, src/e7ac/catalog/sets.py). One
  bonus per completed set (MECH-GEAR-07). Combat-only sets add nothing.
- Artifact (MECH-ART-01 `verified`, MECH-ART-02 `community`): v(L) = v0 + (v30 - v0) · L / 30, with v0 = catalog
  `*_min` and v30 = catalog `*_max`. A `*_min` of 0 means the artifact lacks that stat (positional fields,
  docs/DATA_SOURCES.md).
- Imprint (MECH-IMP-02/03): a self imprint is added to the hero; a team imprint is not.
- EE (MECH-EE-01): stat and value come from the build only. The catalog `ee.value` is `assumed` (NV-08) and never
  used; the catalog `ee.stat` only tells which stat an unread EE could feed.
- Display (MECH-STAT-08): a flat stat shows floor(exact total). Rates show one decimal of percent. Crit
  Chance is capped at 100% (MECH-STAT-05), and a capped CC cannot verify its terms.

Evidence (spikes/m7_stat_composition.py, the user's Hero Info captures of 2026-10-04 at scales 0.64/1.0/1.28):
- Haru, Lady of the Scales, Ainz and Straze: 36/36 displayed stats reproduced.
- floor(total) fits 16/16 flat stats; round-half-up fits only 10/16.
- Single-field gear errors (digit, "%", icon family): 6087 of 6336 caught; the 249 missed were all Crit Chance fields
  of the capped Straze. The failing stats were always exactly the ones the wrong field feeds.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import Final, Literal

from pydantic import JsonValue

from e7ac.catalog.facts import EntityType
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.domain.codes import DataStatus, Stat
from e7ac.domain.roster import GearSlot, HeroBuild, ImprintMode

Verdict = Literal["ok", "capped", "mismatch", "unknown"]

DISPLAYED_STATS: Final = (
    Stat.ATK,
    Stat.DEF,
    Stat.HP,
    Stat.SPEED,
    Stat.CRIT_CHANCE,
    Stat.CRIT_DAMAGE,
    Stat.EFFECTIVENESS,
    Stat.EFFECT_RESISTANCE,
    Stat.DUAL_ATTACK,
)
"""The nine stats of the stats panel, in screen order."""

# MECH-STAT-03: catalog base stats are Lv60 6-star fully awakened; any other build has a different base.
BASE_LEVEL: Final = 60
BASE_STARS: Final = 6
BASE_AWAKENING: Final = 6
# MECH-ART-01 (`verified`): artifacts enhance to +30, and the +30 value is 13 x the +0 value.
ARTIFACT_MAX_ENHANCE: Final = 30
ARTIFACT_MAX_FACTOR: Final = 13
# MECH-STAT-05 (`verified`; seen again on Straze, composed 110% shown 100.0%): Crit Chance is displayed capped at 100%.
CRIT_CHANCE_CAP: Final = Decimal(1)
RATE_TOLERANCE: Final = Decimal("0.0005")
"""Half the 0.1% display step of rates: every exact rate on the captures had a residual of 0."""
EXACT_QUANTUM: Final = Decimal("0.000001")
"""The exact total is rounded to this before the display floor. Real totals are multiples of 0.01 (integer bases
times whole-percent terms, artifact values with one decimal), so this rounding never moves one across an integer.
It only absorbs a non-terminating division (an artifact off the 13x rule) or noise in the inputs: 1139.9999999
counts as 1140."""

_FINAL_FIELD: Final[Mapping[Stat, str]] = {
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
_FLAT: Final = frozenset({Stat.ATK, Stat.DEF, Stat.HP, Stat.SPEED})
_PERCENT_OF: Final[Mapping[Stat, Stat]] = {
    Stat.ATK_PERCENT: Stat.ATK,
    Stat.DEF_PERCENT: Stat.DEF,
    Stat.HP_PERCENT: Stat.HP,
}
_ARTIFACT_STATS: Final = (("atk", Stat.ATK), ("def", Stat.DEF), ("hp", Stat.HP))
_WEAK_STATUSES: Final = frozenset({DataStatus.ASSUMED, DataStatus.UNKNOWN})
BASE_SUSPECT: Final = "base"
_SCREEN_READ: Final = ("gear.", "imprint", "exclusive_equipment")
"""Components whose stat is read from the screen (icon or text), so a misread can move them to another stat."""
_LABELS: Final[Mapping[Stat, str]] = {
    Stat.ATK: "Attack",
    Stat.DEF: "Defense",
    Stat.HP: "Health",
    Stat.SPEED: "Speed",
    Stat.CRIT_CHANCE: "Crit Chance",
    Stat.CRIT_DAMAGE: "Crit Damage",
    Stat.EFFECTIVENESS: "Effectiveness",
    Stat.EFFECT_RESISTANCE: "Effect Resistance",
    Stat.DUAL_ATTACK: "Dual Attack Chance",
}


@dataclass(frozen=True, slots=True)
class StatCheck:
    """One displayed stat against its composition.

    `suspects` (empty when "ok") names the components the verdict is about:
    - "mismatch": the displayed value or one of the suspects is wrong. First the stat's own components; then the
      screen-read fields (gear values, imprint, EE) of the other stats that failed or could not be verified, because
      a value read with the wrong icon feeds the wrong stat; then "base";
    - "capped": the stat's components, which the cap hides;
    - "unknown": the components of unknown amount, presence or target first, then the stat's own components.
    Labels: "gear.boots.sub3", "set set_speed", "artifact efm30+4", "imprint", "exclusive_equipment", "base". Components
    that add nothing carry a note in brackets ("imprint (team, not added)").
    """

    stat: Stat
    expected: float
    """Exact MECH-STAT-02 total from the known components (uncapped; rates as fractions)."""
    displayed: float
    verdict: Verdict
    suspects: tuple[str, ...]


@dataclass(slots=True)
class CompositionReport:
    applicable: bool
    reason: str | None
    """Why the check could not run (applicable is False)."""
    checks: list[StatCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.applicable and not any(check.verdict == "mismatch" for check in self.checks)


@dataclass(frozen=True, slots=True)
class _Term:
    stat: Stat
    """The displayed stat it feeds."""
    value: Decimal
    of_base: bool
    """True: a fraction of the base stat (ATK%/DEF%/HP%, speed sets). False: added in display units."""
    source: str


@dataclass(slots=True)
class _Composition:
    terms: list[_Term] = field(default_factory=list)
    inert: defaultdict[Stat, list[str]] = field(default_factory=lambda: defaultdict(list))
    """Components that add nothing to a stat but would if misread (team imprint, incomplete set): suspects only."""
    possible: defaultdict[Stat, list[str]] = field(default_factory=lambda: defaultdict(list))
    """Components that may or may not feed a stat (not on the build, target unknown): a stat that does not fit is
    "unknown" rather than a mismatch."""
    unknown: defaultdict[Stat, list[str]] = field(default_factory=lambda: defaultdict(list))
    """Components that feed a stat by an unknown amount: the stat is not checked."""
    warnings: list[str] = field(default_factory=list)

    def add(self, stat: Stat, value: Decimal, source: str, *, of_base: bool | None = None) -> None:
        """Add a component in game units (stat code as stored: ATK% feeds ATK as a fraction of base)."""
        target = _PERCENT_OF.get(stat, stat)
        self.terms.append(_Term(target, value, stat in _PERCENT_OF if of_base is None else of_base, source))

    def sources(self, stat: Stat) -> list[str]:
        return [term.source for term in self.terms if term.stat is stat]


def check_final_stats(
    build: HeroBuild,
    hero: ResolvedEntity,
    *,
    artifact: ResolvedEntity | None,
    sets: Mapping[str, ResolvedEntity],
) -> CompositionReport:
    """Recompose the nine displayed stats of `build` and compare (MECH-STAT-02, MECH-STAT-08).

    `hero`: catalog hero entity (`base.<stat>`, `imprint.stat`, `ee.stat`). `artifact`: catalog entity of
    `build.artifact`, or None when the catalog lacks it. `sets`: catalog set entities by code (`pieces`,
    `static_bonus`). Raises ValueError when the entities do not belong to the build (caller bug).
    """
    _check_arguments(build, hero, artifact, sets)
    base, missing = _base_stats(hero)
    reason = _not_applicable(build, hero.entity_id, missing)
    if reason is not None:
        return CompositionReport(applicable=False, reason=reason)
    composition = _Composition()
    _base_warnings(hero, composition)
    _gear_terms(build, composition)
    _set_terms(build, sets, composition)
    _artifact_terms(build, artifact, composition)
    _imprint_terms(build, hero, composition)
    _ee_terms(build, hero, composition)
    report = CompositionReport(applicable=True, reason=None, warnings=composition.warnings)
    exact = {stat: _compose(stat, base[stat], composition.terms) for stat in DISPLAYED_STATS}
    shown = {stat: _displayed(build, stat) for stat in DISPLAYED_STATS}
    verdicts = {stat: _verdict(stat, exact[stat], shown[stat], composition) for stat in DISPLAYED_STATS}
    for stat in DISPLAYED_STATS:
        check = StatCheck(
            stat=stat,
            expected=float(exact[stat]),
            displayed=float(shown[stat]),
            verdict=verdicts[stat],
            suspects=_suspects(stat, verdicts, composition),
        )
        report.checks.append(check)
        warning = _warning(check, exact[stat], shown[stat], _depends(stat, composition))
        if warning is not None:
            report.warnings.append(warning)
    return report


# ------------------------------------------------------------------------------------------------ applicability


def _check_arguments(
    build: HeroBuild, hero: ResolvedEntity, artifact: ResolvedEntity | None, sets: Mapping[str, ResolvedEntity]
) -> None:
    if hero.entity_type is not EntityType.HERO or hero.entity_id != build.hero_code:
        raise ValueError(f"catalog entity {hero.entity_type.value} {hero.entity_id} is not hero {build.hero_code}")
    if artifact is not None:
        ref = build.artifact
        if artifact.entity_type is not EntityType.ARTIFACT or ref is None or artifact.entity_id != ref.code:
            equipped = ref.code if ref is not None else "none"
            raise ValueError(f"catalog artifact {artifact.entity_id} is not the build's artifact ({equipped})")
    for code, entity in sets.items():
        if entity.entity_type is not EntityType.SET or entity.entity_id != code:
            raise ValueError(f"sets[{code!r}] is catalog {entity.entity_type.value} {entity.entity_id}")


def _base_stats(hero: ResolvedEntity) -> tuple[dict[Stat, Decimal], list[Stat]]:
    base: dict[Stat, Decimal] = {}
    missing: list[Stat] = []
    for stat in DISPLAYED_STATS:
        value = _decimal(hero.value(f"base.{stat.value}"))
        if value is None:
            missing.append(stat)
        else:
            base[stat] = value
    return base, missing


def _not_applicable(build: HeroBuild, code: str, missing_base: list[Stat]) -> str | None:
    if build.final_stats is None:
        return "the build has no displayed final stats"
    if (build.level, build.stars, build.awakening) != (BASE_LEVEL, BASE_STARS, BASE_AWAKENING):
        return (
            f"the catalog base stats are Lv{BASE_LEVEL} {BASE_STARS}-star with awakening {BASE_AWAKENING} "
            f"(MECH-STAT-03); this build is Lv{build.level} {build.stars}-star with awakening {build.awakening}"
        )
    if missing_base:
        return f"the catalog has no base {', '.join(s.value for s in missing_base)} for {code}"
    absent = [slot.value for slot in GearSlot if slot not in build.gear]
    if absent:
        return f"gear missing: {', '.join(absent)} (all six pieces are needed)"
    return None


def _base_warnings(hero: ResolvedEntity, composition: _Composition) -> None:
    for stat in DISPLAYED_STATS:
        status = hero.status(f"base.{stat.value}")
        if status in _WEAK_STATUSES:
            composition.warnings.append(
                f"catalog base {stat.value} of {hero.entity_id} is {status.value} (sources disagree, NV-15): "
                "a mismatch on it may be the catalog's"
            )


# ------------------------------------------------------------------------------------------------ components


def _gear_terms(build: HeroBuild, composition: _Composition) -> None:
    for slot in GearSlot:
        gear = build.gear[slot]
        composition.add(gear.main.stat, _exact(gear.main.value), f"gear.{slot.value}.main")
        for index, sub in enumerate(gear.substats, start=1):
            composition.add(sub.stat, _exact(sub.value), f"gear.{slot.value}.sub{index}")


def _set_terms(build: HeroBuild, sets: Mapping[str, ResolvedEntity], composition: _Composition) -> None:
    """MECH-GEAR-05/06/07: one static bonus per completed set; leftover pieces of a set are inert suspects."""
    counts = Counter(gear.set_code for gear in build.gear.values())
    for code, count in sorted(counts.items()):
        entity = sets.get(code)
        required = _whole(entity.value("pieces")) if entity is not None else None
        bonuses = _static_bonuses(entity.value("static_bonus")) if entity is not None else None
        if entity is None or required is None or bonuses is None:
            for stat in DISPLAYED_STATS:
                composition.possible[stat].append(f"set {code} ({_set_gap(entity, required)})")
            continue
        times, left = divmod(count, required)
        for stat, value, of_base in bonuses:
            if times:
                label = f"set {code}" if times == 1 else f"set {code} x{times}"
                composition.add(stat, value * times, label, of_base=of_base)
            if left:
                composition.inert[_PERCENT_OF.get(stat, stat)].append(
                    f"set {code} ({left} of {required} pieces, no bonus)"
                )


def _set_gap(entity: ResolvedEntity | None, required: int | None) -> str:
    if entity is None:
        return "not in the catalog"
    return "piece count unknown" if required is None else "static bonus unknown"


def _artifact_terms(build: HeroBuild, artifact: ResolvedEntity | None, composition: _Composition) -> None:
    """MECH-ART-01/02: linear between the catalog +0 and +30 values."""
    ref = build.artifact
    if ref is None or artifact is None:
        note = "none on the build" if ref is None else "not in the catalog"
        label = "artifact" if ref is None else f"artifact {ref.code}+{ref.level}"
        for _, stat in _ARTIFACT_STATS:
            composition.possible[stat].append(f"{label} ({note})")
        return
    label = f"artifact {ref.code}+{ref.level}"
    for key, stat in _ARTIFACT_STATS:
        low = _decimal(artifact.value(f"{key}_min"))
        if low is None:
            composition.possible[stat].append(f"{label} ({key} unknown)")
            continue
        high = _decimal(artifact.value(f"{key}_max"))
        if high is None:
            high = low * ARTIFACT_MAX_FACTOR
            if low:
                composition.warnings.append(f"{label}: no +30 {key} in the catalog, 13 x +0 used (MECH-ART-01)")
        elif high != low * ARTIFACT_MAX_FACTOR:
            composition.warnings.append(f"{label}: +30 {key} {high} is not 13 x +0 {low} (MECH-ART-01)")
        if not low and not high:
            continue  # positional fields: this artifact has no such stat
        for name in (f"{key}_min", f"{key}_max"):
            if name in artifact.fields and artifact.status(name) in _WEAK_STATUSES:
                composition.warnings.append(f"{label}: catalog {name} is {artifact.status(name).value} (NV-16)")
        composition.add(stat, low + (high - low) * ref.level / ARTIFACT_MAX_ENHANCE, label)


def _imprint_terms(build: HeroBuild, hero: ResolvedEntity, composition: _Composition) -> None:
    """MECH-IMP-02/03: only a self imprint is part of the hero's own stats."""
    imprint = build.imprint
    if imprint is None:
        own = _stat(hero.value("imprint.stat"))
        if own is not None:
            composition.possible[_PERCENT_OF.get(own, own)].append("imprint (none on the build)")
        return
    target = _PERCENT_OF.get(imprint.stat, imprint.stat)
    if imprint.mode is ImprintMode.SELF:
        composition.add(imprint.stat, _exact(imprint.value), "imprint")
    elif imprint.mode is ImprintMode.TEAM:
        composition.inert[target].append("imprint (team, not added)")
    else:
        composition.possible[target].append("imprint (self or team unknown, not added)")


def _ee_terms(build: HeroBuild, hero: ResolvedEntity, composition: _Composition) -> None:
    """MECH-EE-01: stat and value from the build; an incomplete EE makes its stat unknown (NV-08)."""
    ee = build.exclusive_equipment
    catalog_stat = _stat(hero.value("ee.stat"))
    if ee is None:
        if catalog_stat is not None:
            composition.possible[_PERCENT_OF.get(catalog_stat, catalog_stat)].append(
                "exclusive_equipment (none on the build)"
            )
        return
    if ee.stat is not None and ee.value is not None:
        composition.add(ee.stat, _exact(ee.value), "exclusive_equipment")
        if catalog_stat is not None and catalog_stat is not ee.stat:
            composition.warnings.append(
                f"exclusive equipment: the build says {ee.stat.value}, the catalog ee.stat says "
                f"{catalog_stat.value} ({hero.status('ee.stat').value})"
            )
        return
    stat = ee.stat if ee.stat is not None else catalog_stat
    if stat is None:
        for displayed in DISPLAYED_STATS:
            composition.possible[displayed].append("exclusive_equipment (stat unknown)")
        return
    gap = "value unknown" if ee.value is None else f"stat not on the build, catalog says {stat.value}"
    composition.unknown[_PERCENT_OF.get(stat, stat)].append(f"exclusive_equipment ({gap})")


# ------------------------------------------------------------------------------------------------ comparison


def _suspects(stat: Stat, verdicts: Mapping[Stat, Verdict], composition: _Composition) -> tuple[str, ...]:
    verdict = verdicts[stat]
    if verdict == "ok":
        return ()
    own = [*composition.sources(stat), *composition.inert[stat]]
    if verdict == "unknown":
        return _unique([*_depends(stat, composition), *own, BASE_SUSPECT])
    if verdict == "capped":
        return _unique(own)
    others = [other for other in DISPLAYED_STATS if other is not stat and verdicts[other] != "ok"]
    cross = [label for other in others for label in composition.sources(other) if label.startswith(_SCREEN_READ)]
    return _unique([*own, *cross, BASE_SUSPECT])


def _depends(stat: Stat, composition: _Composition) -> list[str]:
    return [*composition.unknown[stat], *composition.possible[stat]]


def _verdict(stat: Stat, exact: Decimal, displayed: Decimal, composition: _Composition) -> Verdict:
    """An unknown amount on the stat: not checked. A stat that may be fed by a component of unknown presence or
    target is checked without it: a fit is "ok" (the component is not there), a misfit is "unknown"."""
    if composition.unknown[stat]:
        return "unknown"
    fit = _fit(stat, exact, displayed)
    if fit == "mismatch" and composition.possible[stat]:
        return "unknown"
    return fit


def _warning(check: StatCheck, exact: Decimal, displayed: Decimal, depends: list[str]) -> str | None:
    label, shown, composed = _LABELS[check.stat], _show(check.stat, displayed), _show(check.stat, exact)
    if check.verdict == "mismatch":
        return (
            f"{label} mismatch: shown {shown}, composition gives {composed} (MECH-STAT-02); "
            f"the displayed value or one of these is wrong: {', '.join(check.suspects)}"
        )
    if check.verdict == "unknown":
        return (
            f"{label} not checked: shown {shown}, the known components give {composed}; "
            f"it depends on {', '.join(depends)}"
        )
    if check.verdict == "capped":
        return f"{label} shown at the 100% cap with {composed} composed (MECH-STAT-05): its components are not verified"
    return None


def _compose(stat: Stat, base: Decimal, terms: Iterable[_Term]) -> Decimal:
    """MECH-STAT-02, exact: base · (1 + Σ of-base fractions) + Σ added values."""
    of_base = Decimal(0)
    added = Decimal(0)
    for term in terms:
        if term.stat is stat:
            if term.of_base:
                of_base += term.value
            else:
                added += term.value
    return base * (1 + of_base) + added


def _fit(stat: Stat, exact: Decimal, displayed: Decimal) -> Literal["ok", "capped", "mismatch"]:
    if stat in _FLAT:
        shown = exact.quantize(EXACT_QUANTUM, rounding=ROUND_HALF_EVEN).to_integral_value(rounding=ROUND_FLOOR)
        return "ok" if shown == displayed else "mismatch"  # MECH-STAT-08: floor of the exact total
    if stat is Stat.CRIT_CHANCE and exact >= CRIT_CHANCE_CAP:
        return "capped" if abs(displayed - CRIT_CHANCE_CAP) <= RATE_TOLERANCE else "mismatch"
    return "ok" if abs(displayed - exact) <= RATE_TOLERANCE else "mismatch"


# ------------------------------------------------------------------------------------------------ helpers


def _displayed(build: HeroBuild, stat: Stat) -> Decimal:
    assert build.final_stats is not None  # applicability checked first
    value: float = getattr(build.final_stats, _FINAL_FIELD[stat])
    return _exact(value)


def _exact(value: float) -> Decimal:
    """The decimal the value was written as (0.13, not 0.130000000000000004440892098500626)."""
    return Decimal(repr(value))


def _decimal(value: JsonValue) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = _exact(value) if isinstance(value, float) else Decimal(value)
    return number if number.is_finite() else None


def _whole(value: JsonValue) -> int | None:
    number = _decimal(value)
    if number is None or number <= 0 or number != number.to_integral_value():
        return None
    return int(number)


def _stat(value: JsonValue) -> Stat | None:
    if not isinstance(value, str):
        return None
    try:
        return Stat(value)
    except ValueError:
        return None


def _static_bonuses(value: JsonValue) -> list[tuple[Stat, Decimal, bool]] | None:
    """Catalog `static_bonus` [{stat, value, of_base}] (src/e7ac/catalog/sets.py); None when malformed."""
    if not isinstance(value, list):
        return None
    bonuses: list[tuple[Stat, Decimal, bool]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        stat, amount, of_base = _stat(item.get("stat")), _decimal(item.get("value")), item.get("of_base")
        if stat is None or amount is None or not isinstance(of_base, bool):
            return None
        bonuses.append((stat, amount, of_base))
    return bonuses


def _show(stat: Stat, value: Decimal) -> str:
    if stat in _FLAT:
        return f"{value.normalize():f}" if value == value.to_integral_value() else f"{float(value):.2f}"
    return f"{float(value) * 100:.2f}%"


def _unique(labels: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(labels))
