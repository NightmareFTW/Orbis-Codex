"""Import a Fribbels Optimizer save file (M4, path A of SPEC D45): the user runs Fribbels and its own importer, saves
"Save all optimizer data", and this module reads that local JSON file. Orbis Codex never captures game traffic.

File format, derived from Fribbels' code (status `community` until checked against a real save of the user's):
- `app/js/lib/saves.js`: `{"heroes": [...], "items": [...]}`, the backend objects serialised by Gson;
- items (`backend/.../model/Item.java`): `gear` ("Weapon"…), `rank` ("Epic"…), `set` ("SpeedSet"…), `enhance`,
  `level` (item level), `main` and `substats` (`model/Stat.java`: `type` "AttackPercent"…, integer `value` with rates
  in percent, `rolls`, `modified`), `id`, `ingameId` (the game's item id), `equippedById` (a Fribbels hero id);
- heroes (`model/Hero.java`): `id`, `name`, `stars`, plus bonuses the user types into Fribbels ("Add Artifact/EE/
  Imprint bonus stats"): `artifactName`/`artifactLevel`, `imprintNumber` (the hero's own imprint value, from
  `self_devotion`), `eeNumber` — all strings, "None" when unset (`app/js/lib/dialog.js`).

What is NOT taken, on purpose:
- Fribbels' hero stats and CP: computed by Fribbels (with reforge previews), not read from the game; the displayed
  stats stay a screen reading (MECH-STAT-01);
- the item `wss` score: Fribbels' own metric, not the game's piece score;
- awakening and level: not in Fribbels' hero model (the importer drops them; Fribbels assumes max awakening). They are
  kept from the roster, or assumed with confidence 0 and reported;
- unequipped items (inventory): counted only; the roster stores builds.
Fribbels' importer keeps one hero per name and only items from a chosen "+N" up (scanner.js / ItemsRequestHandler):
a slot missing from the save keeps the roster's piece, with a note.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, ValidationError

from e7ac.catalog.names import NameIndex
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.domain.codes import SourceId, Stat
from e7ac.domain.roster import (
    ArtifactRef,
    BuildSource,
    ExclusiveEquipment,
    Gear,
    GearGrade,
    GearSlot,
    HeroBuild,
    Imprint,
    ImprintGrade,
    ImprintMode,
    StatValue,
    Substat,
)
from e7ac.sources.fribbels import SET_PIECES

STAT_TYPES: Final[Mapping[str, Stat]] = {
    "Attack": Stat.ATK,
    "Health": Stat.HP,
    "Defense": Stat.DEF,
    "AttackPercent": Stat.ATK_PERCENT,
    "HealthPercent": Stat.HP_PERCENT,
    "DefensePercent": Stat.DEF_PERCENT,
    "CriticalHitChancePercent": Stat.CRIT_CHANCE,
    "CriticalHitDamagePercent": Stat.CRIT_DAMAGE,
    "EffectivenessPercent": Stat.EFFECTIVENESS,
    "EffectResistancePercent": Stat.EFFECT_RESISTANCE,
    "Speed": Stat.SPEED,
}
"""Fribbels `enums/StatType.java` names (its "Dac" is never a gear stat and is refused)."""
SLOTS: Final[Mapping[str, GearSlot]] = {slot.value.capitalize(): slot for slot in GearSlot}
"""Fribbels `enums/Gear.java`: "Weapon", "Helmet", "Armor", "Necklace", "Ring", "Boots"."""
RANKS: Final[Mapping[str, GearGrade]] = {grade.value.capitalize(): grade for grade in GearGrade}
"""Fribbels `enums/Rank.java`: "Normal", "Good", "Rare", "Heroic", "Epic"."""
SETS: Final[Mapping[str, str]] = {name: code for code, (name, _) in SET_PIECES.items()}
"""Fribbels set names ("SpeedSet") -> catalog set codes (`enums/Set.java`, MECH-GEAR-05)."""
UNSET: Final = frozenset({"", "none", "null"})
USER_ENTERED: Final = 0.7
"""Confidence of the artifact, imprint and EE a user typed into Fribbels (not read from the game)."""
ASSUMED: Final = 0.0


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class FribbelsStat(_Model):
    type: str
    value: float
    rolls: int | None = None
    modified: bool | None = None


class FribbelsItem(_Model):
    id: str | None = None
    ingameId: str | None = None  # noqa: N815 - the save file's own key
    gear: str
    rank: str
    set: str
    enhance: int
    level: int
    main: FribbelsStat
    substats: list[FribbelsStat] = []
    equippedById: str | None = None  # noqa: N815


class FribbelsHero(_Model):
    id: str
    name: str
    stars: int | None = None
    artifactName: str | None = None  # noqa: N815
    artifactLevel: str | int | None = None  # noqa: N815
    imprintNumber: str | float | None = None  # noqa: N815
    eeNumber: str | float | None = None  # noqa: N815


@dataclass(slots=True)
class HeroImport:
    """One hero of the save: a build to store, or the reason it cannot be."""

    name: str
    fribbels_id: str
    hero_code: str | None = None
    build: HeroBuild | None = None
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FribbelsSave:
    heroes: list[HeroImport] = field(default_factory=list)
    unequipped_items: int = 0
    warnings: list[str] = field(default_factory=list)
    """Entries of the file that could not be read (each named), never dropped silently."""


class FribbelsFileError(ValueError):
    """The file is not a Fribbels save (no `heroes` and `items` lists)."""


def read_fribbels_save(
    text: str,
    heroes: Mapping[str, ResolvedEntity],
    artifacts: Mapping[str, ResolvedEntity],
    *,
    captured_at: datetime,
) -> FribbelsSave:
    """Builds for every hero of a Fribbels save. `heroes`/`artifacts`: catalog entities by code."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FribbelsFileError(f"not a JSON file: {exc.msg} (line {exc.lineno})") from exc
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("heroes"), list)
        or not isinstance(data.get("items"), list)
    ):
        raise FribbelsFileError('not a Fribbels save: "heroes" and "items" lists expected ("Save all optimizer data")')
    save = FribbelsSave()
    items = _items(data["items"], save.warnings)
    names = hero_names(heroes)
    artifact_codes = _artifact_codes(artifacts)
    equipped: dict[str, list[tuple[FribbelsItem, Gear]]] = {}
    for item, gear in items:
        if item.equippedById:
            equipped.setdefault(item.equippedById, []).append((item, gear))
        else:
            save.unequipped_items += 1
    for index, raw in enumerate(data["heroes"]):
        try:
            hero = FribbelsHero.model_validate(raw)
        except ValidationError as exc:
            save.warnings.append(f"hero #{index + 1}: not read ({_first_error(exc)})")
            continue
        entry = HeroImport(name=hero.name, fribbels_id=hero.id)
        save.heroes.append(entry)
        _hero(entry, hero, names, heroes, artifact_codes, equipped.get(hero.id, []), captured_at)
    for unknown in sorted(set(equipped) - {h.fribbels_id for h in save.heroes}):
        save.warnings.append(f"{len(equipped[unknown])} item(s) equipped by an unknown hero id {unknown}: ignored")
    return save


def hero_names(heroes: Mapping[str, ResolvedEntity]) -> NameIndex:
    """Exact names of the catalog heroes: the chosen name plus the name Fribbels gives the hero when the sources
    disagree (the save uses Fribbels' names). A name shared by several heroes stays ambiguous and is never matched."""
    names = NameIndex()
    for code, entity in heroes.items():
        names.add(entity.name, code)
        resolved = entity.fields.get("name")
        for alternative in resolved.alternatives if resolved is not None else ():
            if SourceId.FRIBBELS in alternative.sources and isinstance(alternative.value, str):
                names.add(alternative.value, code)
    return names


def gear_of(item: FribbelsItem) -> Gear:
    """The domain gear piece of a Fribbels item (ValueError naming the field when it cannot be mapped)."""
    slot = _mapped(SLOTS, item.gear, "gear slot")
    grade = _mapped(RANKS, item.rank, "rank")
    set_code = _mapped(SETS, item.set, "set")
    main = _stat(item.main)
    substats = tuple(
        Substat(stat=stat.stat, value=stat.value, rolls=s.rolls, modified=bool(s.modified))
        for s in item.substats
        for stat in (_stat(s),)
    )
    external = f"ingame:{item.ingameId}" if item.ingameId else (f"fribbels:{item.id}" if item.id else None)
    return Gear(
        slot=slot,
        set_code=set_code,
        grade=grade,
        item_level=item.level,
        enhance=item.enhance,
        main=main,
        substats=substats,
        external_id=external,
    )


# ------------------------------------------------------------------------------------------------ internals


def _items(raw_items: Sequence[Any], warnings: list[str]) -> list[tuple[FribbelsItem, Gear]]:
    items: list[tuple[FribbelsItem, Gear]] = []
    for index, raw in enumerate(raw_items):
        try:
            item = FribbelsItem.model_validate(raw)
            items.append((item, gear_of(item)))
        except ValidationError as exc:
            warnings.append(f"item #{index + 1}: not read ({_first_error(exc)})")
        except ValueError as exc:
            warnings.append(f"item #{index + 1}: not read ({exc})")
    return items


def _hero(
    entry: HeroImport,
    hero: FribbelsHero,
    names: NameIndex,
    heroes: Mapping[str, ResolvedEntity],
    artifact_codes: Mapping[str, str],
    pieces: Sequence[tuple[FribbelsItem, Gear]],
    captured_at: datetime,
) -> None:
    if names.is_ambiguous(hero.name):
        entry.problems.append(f"several catalog heroes are called {hero.name!r}: not imported (enter it by code)")
        return
    code = names.lookup(hero.name)  # a database name, not OCR: exact (normalised) only, never fuzzy
    if code is None:
        entry.problems.append(f"no catalog hero is called {hero.name!r} (run e7 catalog sync)")
        return
    entry.hero_code = code
    confidence: dict[str, float] = {}
    stars = hero.stars if hero.stars in range(1, 7) else None
    if stars is None:
        entry.notes.append(f"stars {hero.stars!r} not usable: assumed 6")
        stars = 6
        confidence["stars"] = ASSUMED
    gear: dict[GearSlot, Gear] = {}
    for item, piece in pieces:
        if piece.slot in gear:
            entry.notes.append(f"two {piece.slot.value} pieces equipped in the save: {item.id} ignored")
            continue
        gear[piece.slot] = piece
    data: dict[str, Any] = {
        "hero_code": code,
        "stars": stars,
        "awakening": stars,
        "level": stars * 10,  # MECH-HERO-01: Fribbels models max level / max awakening, the save has neither
        "gear": gear,
        "captured_at": captured_at,
        "source": BuildSource.FRIBBELS,
        "confidence": {**confidence, "awakening": ASSUMED, "level": ASSUMED},
        "artifact": _artifact(hero, artifact_codes, entry, confidence),
        "imprint": _imprint(hero, heroes[code], entry, confidence),
        "exclusive_equipment": _exclusive(hero, heroes[code], entry, confidence),
    }
    data["confidence"].update(confidence)
    try:
        entry.build = HeroBuild.model_validate(data)
    except ValidationError as exc:
        entry.problems.append(f"not a valid build: {_first_error(exc)}")


def _artifact(
    hero: FribbelsHero, codes: Mapping[str, str], entry: HeroImport, confidence: dict[str, float]
) -> ArtifactRef | None:
    if _unset(hero.artifactName):
        return None
    assert hero.artifactName is not None
    code = codes.get(hero.artifactName.strip())
    level = _number(hero.artifactLevel)
    if code is None or level is None or not level.is_integer() or not 0 <= level <= 30:
        entry.notes.append(f"artifact {hero.artifactName!r} +{hero.artifactLevel} not usable: not stored")
        return None
    confidence["artifact"] = USER_ENTERED
    return ArtifactRef(code=code, level=int(level))


def _imprint(
    hero: FribbelsHero, entity: ResolvedEntity, entry: HeroImport, confidence: dict[str, float]
) -> Imprint | None:
    """Fribbels' imprint field is the hero's own (self) imprint value, typed by the user (dialog.js)."""
    value = _number(hero.imprintNumber)
    if value is None:
        return None
    stat_code, grades = entity.value("imprint.stat"), entity.value("imprint.values")
    if not isinstance(stat_code, str) or stat_code not in Stat._value2member_map_:
        entry.notes.append(f"imprint {hero.imprintNumber!r}: the catalog has no imprint stat for this hero")
        return None
    stat = Stat(stat_code)
    amount = round(value / 100, 6) if stat.is_rate else value
    matches = [
        g
        for g, v in (grades.items() if isinstance(grades, dict) else ())
        if isinstance(v, (int, float)) and abs(v - amount) < 1e-6 and g in ImprintGrade._value2member_map_
    ]
    grade = ImprintGrade(matches[0]) if len(matches) == 1 else None
    confidence["imprint"] = confidence["imprint.mode"] = USER_ENTERED
    confidence["imprint.grade"] = USER_ENTERED if grade is not None else ASSUMED
    return Imprint(grade=grade, stat=stat, value=amount, mode=ImprintMode.SELF)


def _exclusive(
    hero: FribbelsHero, entity: ResolvedEntity, entry: HeroImport, confidence: dict[str, float]
) -> ExclusiveEquipment | None:
    value = _number(hero.eeNumber)
    if value is None:
        return None
    stat_code = entity.value("ee.stat")
    if not isinstance(stat_code, str) or stat_code not in Stat._value2member_map_:
        entry.notes.append(f"exclusive equipment {hero.eeNumber!r}: the catalog has no EE stat for this hero")
        return None
    stat = Stat(stat_code)
    confidence["exclusive_equipment"] = USER_ENTERED
    return ExclusiveEquipment(stat=stat, value=round(value / 100, 6) if stat.is_rate else value)


def _artifact_codes(artifacts: Mapping[str, ResolvedEntity]) -> dict[str, str]:
    """Display name -> code, names shared by several codes left out (never matched)."""
    by_name: dict[str, set[str]] = {}
    for code, entity in artifacts.items():
        if entity.name:
            by_name.setdefault(entity.name.strip(), set()).add(code)
    return {name: next(iter(codes)) for name, codes in by_name.items() if len(codes) == 1}


def _stat(raw: FribbelsStat) -> StatValue:
    stat = _mapped(STAT_TYPES, raw.type, "stat type")
    return StatValue(stat=stat, value=round(raw.value / 100, 6) if stat.is_rate else raw.value)


def _mapped[T](table: Mapping[str, T], key: str, what: str) -> T:
    try:
        return table[key]
    except KeyError:
        raise ValueError(f"unknown {what} {key!r}") from None


def _unset(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip().casefold() in UNSET)


def _number(value: str | float | None) -> float | None:
    if _unset(value):
        return None
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(p) for p in error["loc"])
    return f"{where}: {error['msg']}" if where else str(error["msg"])


def merge_with_current(build: HeroBuild, current: HeroBuild | None) -> tuple[HeroBuild, list[str]]:
    """The imported build on top of the hero's current one (SPEC D52). What the save does not carry is kept: level
    and awakening, skill enhancements, a slot below Fribbels' import threshold, and the displayed stats and CP while
    everything they depend on is unchanged. An artifact, imprint or EE typed into Fribbels never silently replaces a
    different one in the roster (read from the game, or entered here): the roster's is kept, with a note, unless it
    has a lower confidence. Returns the validated build and the notes."""
    if current is None:
        return build, []
    notes: list[str] = []
    confidence = dict(build.confidence)
    update: dict[str, Any] = {"skills": current.skills}
    for name in ("level", "awakening"):
        if confidence.get(name) == ASSUMED:
            update[name] = getattr(current, name)
            _take(confidence, current.confidence, name)
    if update.get("awakening", build.awakening) > build.stars:
        update["awakening"] = build.stars
        notes.append(f"awakening {current.awakening} is above the save's {build.stars} stars: lowered to {build.stars}")
    gear = dict(build.gear)
    for slot, piece in current.gear.items():
        if slot not in gear:
            gear[slot] = piece
            _take(confidence, current.confidence, f"gear.{slot.value}")
            notes.append(f"{slot.value}: not in the save (Fribbels imports only items from a chosen +N up): kept")
    update["gear"] = gear
    for name in ("artifact", "imprint", "exclusive_equipment"):
        mine, theirs = getattr(current, name), getattr(build, name)
        if mine is None:
            continue
        label = name.replace("_", " ")
        if theirs is not None and _bonus_key(mine) != _bonus_key(theirs):
            mine_confidence = current.confidence.get(name, 1.0)
            if mine_confidence < build.confidence.get(name, 1.0):
                notes.append(
                    f"{label}: the save's {_describe(theirs)} replaces the roster's {_describe(mine)} "
                    f"(read with confidence {mine_confidence:.2f})"
                )
                continue
            notes.append(
                f"{label}: the save says {_describe(theirs)}, the roster {_describe(mine)}: kept the roster's "
                "(Fribbels' bonus stats are typed by hand; use e7 roster edit if the save is right)"
            )
        update[name] = mine
        _take(confidence, current.confidence, name)
        if isinstance(mine, Imprint) and isinstance(theirs, Imprint) and _bonus_key(mine) == _bonus_key(theirs):
            filled = {"grade": mine.grade or theirs.grade, "mode": mine.mode or theirs.mode}
            update[name] = mine.model_copy(update=filled)
            for part, known in (("grade", mine.grade), ("mode", mine.mode)):
                if known is None:
                    confidence[f"imprint.{part}"] = build.confidence.get(f"imprint.{part}", 1.0)
    merged = HeroBuild.model_validate({**build.model_dump(), **update, "confidence": confidence})
    if _stat_inputs(merged) == _stat_inputs(current):
        merged = merged.model_copy(update={"final_stats": current.final_stats, "cp": current.cp})
        _take(confidence, current.confidence, "final_stats")
        _take(confidence, current.confidence, "cp")
        merged = HeroBuild.model_validate({**merged.model_dump(), "confidence": confidence})
    elif current.final_stats is not None or current.cp is not None:
        notes.append("the build changed: the displayed stats and CP of the last screen reading are dropped (rescan it)")
    return merged, notes


def _take(target: dict[str, float], source: Mapping[str, float], name: str) -> None:
    """Replace the confidences of `name` and its sub-fields (`name.x`) by those of `source`."""
    for key in [k for k in target if k == name or k.startswith(f"{name}.")]:
        del target[key]
    target.update({k: v for k, v in source.items() if k == name or k.startswith(f"{name}.")})


def _bonus_key(value: ArtifactRef | Imprint | ExclusiveEquipment) -> tuple[object, ...]:
    """What makes two artifacts, imprints or EEs the same bonus (an imprint's grade and mode only describe it)."""
    if isinstance(value, ArtifactRef):
        return value.code, value.level
    return value.stat, None if value.value is None else round(value.value, 6)


def _describe(value: ArtifactRef | Imprint | ExclusiveEquipment) -> str:
    if isinstance(value, ArtifactRef):
        return f"{value.code} +{value.level}"
    if value.stat is None or value.value is None:
        return "an EE with an unknown stat"
    amount = f"{value.value * 100:g}%" if value.stat.is_rate else f"{value.value:g}"
    return f"{value.stat.value} {amount}"


def _stat_inputs(build: HeroBuild) -> tuple[object, ...]:
    """What the displayed stats depend on, as far as a save can change it."""
    gear = sorted((slot.value, _visible(piece)) for slot, piece in build.gear.items())
    imprint = build.imprint and (*_bonus_key(build.imprint), build.imprint.mode)
    ee = build.exclusive_equipment and _bonus_key(build.exclusive_equipment)
    return build.stars, build.awakening, build.level, gear, build.artifact, imprint, ee


def _visible(gear: Gear) -> tuple[object, ...]:
    subs = tuple((s.stat, round(s.value, 6)) for s in gear.substats)
    return gear.set_code, gear.grade, gear.item_level, gear.enhance, gear.main.stat, round(gear.main.value, 6), subs
