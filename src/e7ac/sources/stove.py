"""Stove Strategy Guide (official) — catalog part: hero list, sets, artifacts (docs/DATA_SOURCES.md §1).

Endpoints under https://api.onstove.com/pub-meta/v1.0/epic7/guide/wearing-status; JSON envelope
`{"code": 0, "message": "OK", "value": {...}}`; 50 items per page regardless of `is_paging`.
Facts from here are official → status VERIFIED. Per-hero usage statistics (histograms) belong to the
opponent model (M10) and are not fetched here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from e7ac.catalog.facts import EntityType, Fact, SourceRun
from e7ac.domain.codes import DataStatus, Element, HeroClass, SourceId, is_artifact_code, is_hero_code, is_set_code
from e7ac.domain.world import World
from e7ac.sources.http import CachedHttp, FetchResult

API_BASE: Final = "https://api.onstove.com/pub-meta/v1.0/epic7/guide/wearing-status"
PAGE_SIZE: Final = 50
MAX_PAGES: Final = 60
DEFAULT_MAX_AGE: Final = timedelta(days=7)
_NO_CLASS_LOCK: Final = "NN"


class StoveError(Exception):
    """The Stove API answered with an error or a payload that does not match the expected schema."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class StoveHero(_Model):
    hero_code: str
    hero_name: str
    grade: int = Field(ge=1, le=6)
    job_code: HeroClass
    attribute_code: Element


class StoveSet(_Model):
    equip_code: str
    equip_name: str
    equip_effect: str


class StoveArtifactLevel(_Model):
    """Value of one `@` placeholder at +0 and at max. The API sometimes returns `{}` (value unknown)."""

    lv01: str | None = None
    lv_max: str | None = None


class StoveArtifact(_Model):
    artifact_code: str
    artifact_name: str
    job_code: str
    grade: int = Field(ge=1, le=6)
    ability_attack: float = Field(ge=0)
    ability_defense: float = Field(ge=0)  # actually Health (misnamed by the API, verified against Fribbels)
    enhance_ability_attack: float = Field(ge=0)
    enhance_ability_defense: float = Field(ge=0)
    info_text: str | None = None
    level_list: list[StoveArtifactLevel] = []


class _Page(_Model):
    total_count: int = Field(ge=0)
    current_page: int
    guide_list: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class StoveCatalog:
    heroes: list[StoveHero]
    sets: list[StoveSet]
    artifacts: list[StoveArtifact]
    results: list[FetchResult]
    warnings: list[str]


def fetch_catalog(
    http: CachedHttp, world: World, *, refresh: bool = False, max_age: timedelta = DEFAULT_MAX_AGE
) -> StoveCatalog:
    warnings: list[str] = []
    results: list[FetchResult] = []
    namespace = f"stove/{world.value}"

    def page(endpoint: str, number: int) -> _Page:
        result = http.get_text(
            namespace,
            f"{API_BASE}/{endpoint}",
            {
                "world_code": world.value,
                "lang_code": "en",
                "current_page": str(number),
                "is_paging": "Y",
                "keyword": "",
            },
            max_age=max_age,
            refresh=refresh,
        )
        results.append(result)
        return _parse_page(result)

    heroes = _collect("hero-list", page, StoveHero, warnings)
    sets = _collect("equip-list", page, StoveSet, warnings)
    artifacts = _collect("artifact-list", page, StoveArtifact, warnings)
    return StoveCatalog(heroes=heroes, sets=sets, artifacts=artifacts, results=results, warnings=warnings)


def _collect[T: _Model](
    endpoint: str, page: Callable[[str, int], _Page], model: type[T], warnings: list[str]
) -> list[T]:
    items: dict[str, T] = {}
    rejected: list[str] = []
    total: int | None = None
    for number in range(1, MAX_PAGES + 1):
        current = page(endpoint, number)
        total = current.total_count
        if not current.guide_list:
            break
        for raw in current.guide_list:
            try:
                item = model.model_validate(raw)
            except ValidationError as exc:
                rejected.append(f"{_raw_code(raw)}: {exc.errors()[0]['msg']}")
                continue
            items[_key(item)] = item
        if len(items) + len(rejected) >= total:
            break
    if rejected:
        warnings.append(
            f"stove {endpoint}: {len(rejected)} item(s) skipped (unexpected schema): {'; '.join(rejected[:5])}"
        )
        if not items:
            raise StoveError(f"{endpoint}: no item matched the expected schema ({rejected[0]})")
    if total is not None and len(items) + len(rejected) != total:
        warnings.append(f"stove {endpoint}: collected {len(items) + len(rejected)} items but the API reports {total}")
    return list(items.values())


def _raw_code(raw: dict[str, Any]) -> str:
    for attr in ("hero_code", "equip_code", "artifact_code"):
        if attr in raw:
            return str(raw[attr])
    return "<no code>"


def _key(item: _Model) -> str:
    for attr in ("hero_code", "equip_code", "artifact_code"):
        value = getattr(item, attr, None)
        if isinstance(value, str):
            return value
    raise StoveError(f"item without code: {item!r}")


def _parse_page(result: FetchResult) -> _Page:
    try:
        payload = result.json()
    except ValueError as exc:
        raise StoveError(f"not JSON: {result.url}") from exc
    if not isinstance(payload, dict) or payload.get("code") != 0:
        message = payload.get("message") if isinstance(payload, dict) else None
        raise StoveError(f"Stove API error for {result.url}: {message!r}")
    try:
        return _Page.model_validate(payload.get("value"))
    except ValidationError as exc:
        raise StoveError(f"unexpected page schema for {result.url}: {exc}") from exc


def to_facts(catalog: StoveCatalog) -> tuple[list[Fact], SourceRun]:
    facts: list[Fact] = []
    warnings = list(catalog.warnings)

    def add(entity_type: EntityType, entity_id: str, field: str, value: JsonValue, note: str = "") -> None:
        facts.append(
            Fact(
                entity_type=entity_type,
                entity_id=entity_id,
                field=field,
                value=value,
                source=SourceId.STOVE,
                status=DataStatus.VERIFIED,
                note=note,
            )
        )

    for hero in catalog.heroes:
        if not is_hero_code(hero.hero_code):
            warnings.append(f"stove hero-list: unexpected hero code {hero.hero_code!r} skipped")
            continue
        add(EntityType.HERO, hero.hero_code, "name", hero.hero_name.strip())
        add(EntityType.HERO, hero.hero_code, "element", hero.attribute_code.value)
        add(EntityType.HERO, hero.hero_code, "role", hero.job_code.value)
        add(EntityType.HERO, hero.hero_code, "rarity", hero.grade)

    for item in catalog.sets:
        if not is_set_code(item.equip_code):
            warnings.append(f"stove equip-list: unexpected set code {item.equip_code!r} skipped")
            continue
        add(EntityType.SET, item.equip_code, "name", item.equip_name.strip())
        add(EntityType.SET, item.equip_code, "effect_text", item.equip_effect.strip())

    for art in catalog.artifacts:
        if not is_artifact_code(art.artifact_code):
            warnings.append(f"stove artifact-list: unexpected artifact code {art.artifact_code!r} skipped")
            continue
        code = art.artifact_code
        add(EntityType.ARTIFACT, code, "name", art.artifact_name.strip())
        add(EntityType.ARTIFACT, code, "rarity", art.grade)
        role_lock = None if art.job_code == _NO_CLASS_LOCK else art.job_code
        if role_lock is not None and role_lock not in HeroClass._value2member_map_:
            warnings.append(f"stove artifact {code}: unknown class lock {art.job_code!r}")
        add(EntityType.ARTIFACT, code, "role_lock", role_lock)
        add(EntityType.ARTIFACT, code, "atk_min", art.ability_attack)
        add(EntityType.ARTIFACT, code, "hp_min", art.ability_defense, note="API field 'ability_defense' is Health")
        add(EntityType.ARTIFACT, code, "atk_max", art.enhance_ability_attack)
        add(EntityType.ARTIFACT, code, "hp_max", art.enhance_ability_defense, note="API field is Health")
        if art.info_text:
            add(EntityType.ARTIFACT, code, "effect_text", art.info_text.strip())
        else:
            warnings.append(f"stove artifact {code}: no effect text in the API response")
        add(
            EntityType.ARTIFACT,
            code,
            "effect_levels",
            [{"min": lvl.lv01, "max": lvl.lv_max} for lvl in art.level_list],
            note="per '@' placeholder: value at +0 and at max enhancement",
        )

    digest = hashlib.sha256("".join(r.sha256 for r in catalog.results).encode()).hexdigest()[:16]
    run = SourceRun(
        source=SourceId.STOVE,
        version=f"sha256:{digest}",
        retrieved_at=min((r.fetched_at for r in catalog.results), default=datetime(1970, 1, 1, tzinfo=UTC)),
        from_cache=all(r.from_cache for r in catalog.results),
        stale=any(r.stale for r in catalog.results),
        facts=len(facts),
        warnings=tuple(warnings),
    )
    return facts, run
