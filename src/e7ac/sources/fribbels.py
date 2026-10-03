"""Fribbels E7 Optimizer data (community) — docs/DATA_SOURCES.md §2.

`herodata.json` / `artifactdata.json` from the GitHub repository. Everything here is `community` data, except the
EE stat value whose meaning is unclear (NV-08) and is therefore `assumed`. Set piece counts are not in the JSON;
they come from the optimizer's `enums/Set.java` and are listed in `SET_PIECES` below with that citation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from e7ac.catalog.facts import EntityType, Fact, SourceRun, skill_id
from e7ac.catalog.names import NameIndex
from e7ac.domain.codes import (
    DataStatus,
    Element,
    HeroClass,
    Horoscope,
    SourceId,
    Stat,
    is_artifact_code,
    is_hero_code,
)
from e7ac.sources.http import CachedHttp, FetchResult

RAW_BASE: Final = "https://raw.githubusercontent.com/fribbels/Fribbels-Epic-7-Optimizer/main/data/cache"
HERODATA_URL: Final = f"{RAW_BASE}/herodata.json"
ARTIFACTDATA_URL: Final = f"{RAW_BASE}/artifactdata.json"
DEFAULT_MAX_AGE: Final = timedelta(days=1)

SET_SOURCE_NOTE: Final = "Fribbels backend/src/main/java/com/fribbels/enums/Set.java (commit 4e2f6a0)"
# Stove set code -> (Fribbels save-file set name, pieces required). MECH-GEAR-05.
SET_PIECES: Final[Mapping[str, tuple[str, int]]] = {
    "set_max_hp": ("HealthSet", 2),
    "set_def": ("DefenseSet", 2),
    "set_att": ("AttackSet", 4),
    "set_speed": ("SpeedSet", 4),
    "set_cri": ("CriticalSet", 2),
    "set_acc": ("HitSet", 2),
    "set_cri_dmg": ("DestructionSet", 4),
    "set_vampire": ("LifestealSet", 4),
    "set_counter": ("CounterSet", 4),
    "set_res": ("ResistSet", 2),
    "set_coop": ("UnitySet", 2),
    "set_rage": ("RageSet", 4),
    "set_immune": ("ImmunitySet", 2),
    "set_penetrate": ("PenetrationSet", 2),
    "set_revenge": ("RevengeSet", 4),
    "set_scar": ("InjurySet", 4),
    "set_shield": ("ProtectionSet", 4),
    "set_torrent": ("TorrentSet", 2),
    "set_revenant": ("ReversalSet", 4),
    "set_riposte": ("RiposteSet", 4),
    "set_opener": ("WarfareSet", 4),
    "set_chase": ("PursuitSet", 2),
    "set_weak": ("WeakeningSet", 4),
    "set_might": ("FervorSet", 2),
}

# calculatedStatus keys -> our stat codes (rates are fractions in both).
_BASE_STAT_KEYS: Final = {
    "atk": Stat.ATK,
    "hp": Stat.HP,
    "def": Stat.DEF,
    "spd": Stat.SPEED,
    "chc": Stat.CRIT_CHANCE,
    "chd": Stat.CRIT_DAMAGE,
    "dac": Stat.DUAL_ATTACK,
    "eff": Stat.EFFECTIVENESS,
    "efr": Stat.EFFECT_RESISTANCE,
}
_SKILL_FIELDS: Final = {
    "rate": "rate",
    "pow": "pow",
    "targets": "targets",
    "penetration": "penetration",
    "selfHpScaling": "self_hp_scaling",
    "selfDefScaling": "self_def_scaling",
    "selfSpdScaling": "self_spd_scaling",
    "selfAtkScaling": "self_atk_scaling",
    "extraSelfAtkScaling": "extra_self_atk_scaling",
    "extraSelfDefScaling": "extra_self_def_scaling",
    "extraSelfHpScaling": "extra_self_hp_scaling",
    "increasedValue": "increased_value",
    "cdmgIncrease": "crit_damage_increase",
}
_SKILL_KNOWN_KEYS: Final = frozenset({"hitTypes", "options", "note", *_SKILL_FIELDS})


class FribbelsError(Exception):
    """The Fribbels data files do not match the expected schema."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _StatBlock(_Model):
    atk: float
    hp: float
    spd: float
    def_: float = Field(alias="def")
    chc: float
    chd: float
    dac: float
    eff: float
    efr: float


class _SelfDevotion(_Model):
    type: Stat
    grades: dict[str, float]


class _ExEquipStat(_Model):
    type: Stat
    value: float


class _ExEquip(_Model):
    stat: _ExEquipStat


class FribbelsHero(_Model):
    code: str
    id: str = ""
    name: str
    rarity: int
    attribute: Element
    role: HeroClass
    zodiac: Horoscope
    self_devotion: _SelfDevotion | None = None
    ex_equip: list[_ExEquip] = []
    skills: dict[str, dict[str, JsonValue]] = {}
    lv60: _StatBlock

    @classmethod
    def parse(cls, raw: dict[str, object]) -> FribbelsHero:
        status = raw.get("calculatedStatus")
        lv60 = status.get("lv60SixStarFullyAwakened") if isinstance(status, dict) else None
        data = dict(raw)
        data["id"] = raw.get("_id", "")
        data["lv60"] = lv60
        return cls.model_validate(data)


class _ArtifactStats(_Model):
    attack: float
    health: float
    defense: float = 0.0


class FribbelsArtifact(_Model):
    code: str
    name: str
    rarity: int
    role: str
    stats: _ArtifactStats


@dataclass(frozen=True, slots=True)
class FribbelsData:
    heroes: list[FribbelsHero]
    artifacts: list[FribbelsArtifact]
    results: list[FetchResult]
    warnings: list[str]


def fetch(http: CachedHttp, *, refresh: bool = False, max_age: timedelta = DEFAULT_MAX_AGE) -> FribbelsData:
    hero_result = http.get_text("fribbels", HERODATA_URL, max_age=max_age, refresh=refresh, validate=_json_object)
    artifact_result = http.get_text(
        "fribbels", ARTIFACTDATA_URL, max_age=max_age, refresh=refresh, validate=_json_object
    )
    heroes, artifacts, warnings = parse(hero_result.json(), artifact_result.json())
    return FribbelsData(heroes=heroes, artifacts=artifacts, results=[hero_result, artifact_result], warnings=warnings)


def _json_object(text: str) -> None:
    """Validator applied before caching: the data files are JSON objects keyed by name."""
    if not isinstance(json.loads(text), dict):
        raise ValueError("expected a JSON object")


def parse(herodata: object, artifactdata: object) -> tuple[list[FribbelsHero], list[FribbelsArtifact], list[str]]:
    if not isinstance(herodata, dict) or not isinstance(artifactdata, dict):
        raise FribbelsError("herodata.json and artifactdata.json must be JSON objects keyed by name")
    warnings: list[str] = []
    heroes: list[FribbelsHero] = []
    for name, raw in herodata.items():
        if not isinstance(raw, dict):
            warnings.append(f"fribbels hero {name!r}: not an object, skipped")
            continue
        try:
            heroes.append(FribbelsHero.parse(raw))
            stray = sorted(k for k in raw if k in ("S1", "S2", "S3"))
            if stray:
                warnings.append(f"fribbels hero {name!r}: skill keys outside 'skills' ignored: {', '.join(stray)}")
        except ValidationError as exc:
            warnings.append(f"fribbels hero {name!r} skipped: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}")
    artifacts: list[FribbelsArtifact] = []
    for name, raw in artifactdata.items():
        try:
            artifacts.append(FribbelsArtifact.model_validate(raw))
        except ValidationError as exc:
            warnings.append(f"fribbels artifact {name!r} skipped: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}")
    if not heroes:
        raise FribbelsError("no hero in herodata.json matched the expected schema")
    return heroes, artifacts, warnings


def to_facts(data: FribbelsData) -> tuple[list[Fact], SourceRun]:
    facts: list[Fact] = []
    warnings = list(data.warnings)

    def add(
        entity_type: EntityType,
        entity_id: str,
        field: str,
        value: JsonValue,
        status: DataStatus = DataStatus.COMMUNITY,
        note: str = "",
    ) -> None:
        facts.append(
            Fact(
                entity_type=entity_type,
                entity_id=entity_id,
                field=field,
                value=value,
                source=SourceId.FRIBBELS,
                status=status,
                note=note,
            )
        )

    non_hero = [f"{h.code} {h.name}" for h in data.heroes if not is_hero_code(h.code)]
    if non_hero:
        listed = ", ".join(non_hero)
        warnings.append(f"fribbels: {len(non_hero)} entry(ies) with non-hero codes skipped (monsters?): {listed}")
    unknown_skill_keys: list[str] = []
    for hero in data.heroes:
        code = hero.code
        if not is_hero_code(code):
            continue
        add(EntityType.HERO, code, "name", hero.name.strip())
        if hero.id:
            add(EntityType.HERO, code, "slug", hero.id)
        add(EntityType.HERO, code, "element", hero.attribute.value)
        add(EntityType.HERO, code, "role", hero.role.value)
        add(EntityType.HERO, code, "rarity", hero.rarity)
        add(EntityType.HERO, code, "horoscope", hero.zodiac.value)
        block = hero.lv60.model_dump(by_alias=True)
        for key, stat in _BASE_STAT_KEYS.items():
            add(EntityType.HERO, code, f"base.{stat.value}", block[key], note="Lv60 6-star fully awakened")
        if hero.self_devotion is not None:
            add(EntityType.HERO, code, "imprint.stat", hero.self_devotion.type.value)
            add(EntityType.HERO, code, "imprint.values", dict(hero.self_devotion.grades))
        if hero.ex_equip:
            ee = hero.ex_equip[0].stat
            add(EntityType.HERO, code, "ee.stat", ee.type.value)
            add(
                EntityType.HERO,
                code,
                "ee.value",
                ee.value,
                status=DataStatus.ASSUMED,
                note="meaning of Fribbels ex_equip value unclear (BBK 0.06 vs 12% in game) - NV-08",
            )
            if len(hero.ex_equip) > 1:
                warnings.append(f"fribbels hero {code}: {len(hero.ex_equip)} EE stats, only the first is used")
        for slot_name, skill in hero.skills.items():
            if slot_name not in ("S1", "S2", "S3"):
                continue
            sid = skill_id(code, int(slot_name[1]))
            # e.g. "S1 proc" on an S2 entry: the multipliers describe something else than the plain skill
            skill_note = str(skill.get("note") or "")
            hit_types = skill.get("hitTypes")
            if isinstance(hit_types, list) and hit_types:
                hit_list: list[JsonValue] = [str(h) for h in sorted(str(h) for h in hit_types)]
                add(EntityType.SKILL, sid, "hit_types", hit_list)
            for key, field in _SKILL_FIELDS.items():
                value = skill.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    add(EntityType.SKILL, sid, field, value, note=skill_note)
            for key in sorted(set(skill) - _SKILL_KNOWN_KEYS):
                unknown_skill_keys.append(f"{sid}:{key}")
            options = skill.get("options")
            if isinstance(options, list) and options:
                add(EntityType.SKILL, sid, "variants", options, note="Fribbels skill options (e.g. soulburn/heal)")

    by_code: dict[str, list[FribbelsArtifact]] = {}
    for art in data.artifacts:
        if not is_artifact_code(art.code):
            warnings.append(f"fribbels artifact {art.name!r}: unexpected code {art.code!r} skipped")
            continue
        by_code.setdefault(art.code, []).append(art)
    for code, entries in sorted(by_code.items()):
        candidates: dict[str, list[JsonValue]] = {
            "name": [a.name.strip() for a in entries],
            "rarity": [a.rarity for a in entries],
            "role_lock": [a.role or None for a in entries],
            "atk_min": [a.stats.attack for a in entries],
            "hp_min": [a.stats.health for a in entries],
            "def_min": [a.stats.defense for a in entries],
        }
        disagreeing = [field for field, values in candidates.items() if len({repr(v) for v in values}) > 1]
        if disagreeing:
            names = " / ".join(a.name for a in entries)
            warnings.append(
                f"fribbels artifact {code}: {len(entries)} entries share this code ({names}); "
                f"conflicting fields dropped: {', '.join(disagreeing)}"
            )
        for field, values in candidates.items():
            if field not in disagreeing:
                add(EntityType.ARTIFACT, code, field, values[0])

    if unknown_skill_keys:
        warnings.append(f"fribbels: unmapped skill fields ignored: {', '.join(unknown_skill_keys)}")
    stale_reasons = sorted({r.stale_reason for r in data.results if r.stale and r.stale_reason})
    warnings.extend(f"fribbels: stale cache served ({reason})" for reason in stale_reasons)

    for set_code, (fribbels_name, pieces) in SET_PIECES.items():
        add(EntityType.SET, set_code, "pieces", pieces, note=SET_SOURCE_NOTE)
        add(EntityType.SET, set_code, "fribbels_name", fribbels_name, note=SET_SOURCE_NOTE)

    digest = hashlib.sha256("".join(r.sha256 for r in data.results).encode()).hexdigest()[:16]
    run = SourceRun(
        source=SourceId.FRIBBELS,
        version=f"sha256:{digest}",
        retrieved_at=min(r.fetched_at for r in data.results),
        from_cache=all(r.from_cache for r in data.results),
        stale=any(r.stale for r in data.results),
        facts=len(facts),
        warnings=tuple(warnings),
    )
    return facts, run


def artifact_stat_hints(data: FribbelsData) -> dict[str, dict[str, float]]:
    """Artifact code -> {"atk", "def", "hp"} at +0, for codes whose entries all agree (duplicates excluded)."""
    hints: dict[str, dict[str, float]] = {}
    conflicting: set[str] = set()
    for art in data.artifacts:
        values = {"atk": art.stats.attack, "def": art.stats.defense, "hp": art.stats.health}
        if art.code in hints and hints[art.code] != values:
            conflicting.add(art.code)
        hints.setdefault(art.code, values)
    return {code: values for code, values in hints.items() if code not in conflicting}


def add_to_index(data: FribbelsData, index: NameIndex) -> None:
    """Register slugs and names of real heroes (not monsters) for mapping name-keyed sources such as e7calc."""
    for hero in data.heroes:
        if not is_hero_code(hero.code):
            continue
        for key in (hero.id, hero.name):
            if key:
                index.add(key, hero.code)
