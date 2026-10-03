"""Stove Strategy Guide (official) — catalog part: hero list, sets, artifacts (docs/DATA_SOURCES.md §1).

Endpoints under https://api.onstove.com/pub-meta/v1.0/epic7/guide/wearing-status; JSON envelope
`{"code": 0, "message": "OK", "value": {...}}`; 50 items per page regardless of `is_paging`.
Facts from here are official → status VERIFIED, except where *we* interpret the payload: artifact stat slots are only
`verified` when Fribbels' +0 values confirm which stats they are, and effect values only when every `@` placeholder
has real data (docs/DATA_SOURCES.md §1). Per-hero usage statistics (histograms) belong to the opponent model (M10).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from e7ac.catalog.facts import EntityType, Fact, SourceRun
from e7ac.catalog.sets import set_facts
from e7ac.domain.codes import DataStatus, Element, HeroClass, SourceId, is_artifact_code, is_hero_code, is_set_code
from e7ac.domain.world import World
from e7ac.sources.http import CachedHttp, FetchError, FetchResult

API_BASE: Final = "https://api.onstove.com/pub-meta/v1.0/epic7/guide/wearing-status"
PAGE_SIZE: Final = 50
MAX_PAGES: Final = 60
DEFAULT_MAX_AGE: Final = timedelta(days=7)
_NO_CLASS_LOCK: Final = "NN"
# Stove fills its two artifact stat fields with the artifact's two non-zero stats in this order (observed on all
# 7 real artifacts that have DEF, 2026-10-03); `ability_defense` is NOT always Health.
ARTIFACT_STAT_ORDER: Final = ("atk", "def", "hp")
_SENTINELS: Final = frozenset({"", "0", "0%", "0.0%"})
_CONSISTENCY_WINDOW: Final = timedelta(minutes=10)


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
    ability_attack: float = Field(ge=0)  # first non-zero stat at +0 (see ARTIFACT_STAT_ORDER)
    ability_defense: float = Field(ge=0)  # second non-zero stat at +0 (usually Health, sometimes Defense)
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
    errors: list[str] = field(default_factory=list)
    """Endpoints that failed: the other endpoints' data is still usable."""


def fetch_catalog(
    http: CachedHttp, world: World, *, refresh: bool = False, max_age: timedelta = DEFAULT_MAX_AGE
) -> StoveCatalog:
    """Fetch the three catalog lists. Each endpoint fails independently (its error is recorded, others are kept)."""
    warnings: list[str] = []
    errors: list[str] = []
    results: list[FetchResult] = []
    namespace = f"stove/{world.value}"

    def fetch_endpoint[T: _Model](endpoint: str, model: type[T]) -> list[T]:
        pages: list[tuple[_Page, FetchResult]] = []

        def page(name: str, number: int, force: bool) -> _Page:
            result = http.get_text(
                namespace,
                f"{API_BASE}/{name}",
                {
                    "world_code": world.value,
                    "lang_code": "en",
                    "current_page": str(number),
                    "is_paging": "Y",
                    "keyword": "",
                },
                max_age=max_age,
                refresh=force,
                validate=validate_page,
            )
            parsed = _parse_page(result)
            pages.append((parsed, result))
            return parsed

        items = _collect(endpoint, lambda name, number: page(name, number, refresh), model, warnings)
        if _inconsistent(pages) and not http.offline:
            # pages come from different moments/list versions: fetch the whole list again in one go
            pages.clear()
            items = _collect(endpoint, lambda name, number: page(name, number, True), model, warnings)
        if _inconsistent(pages):
            totals = sorted({p.total_count for p, _ in pages})
            warnings.append(
                f"stove {endpoint}: pages come from different list versions (totals {totals}); "
                "some entries may be missing or duplicated"
            )
        results.extend(r for _, r in pages)
        return items

    def safe[T: _Model](endpoint: str, model: type[T]) -> list[T]:
        try:
            return fetch_endpoint(endpoint, model)
        except (StoveError, FetchError) as exc:
            errors.append(f"{endpoint}: {exc}")
            return []

    heroes = safe("hero-list", StoveHero)
    sets = safe("equip-list", StoveSet)
    artifacts = safe("artifact-list", StoveArtifact)
    if not results and errors:
        raise StoveError("; ".join(errors))
    return StoveCatalog(
        heroes=heroes, sets=sets, artifacts=artifacts, results=results, warnings=warnings, errors=errors
    )


def validate_page(text: str) -> None:
    """Accept only a successful Stove envelope (used before caching, so error pages never replace good data)."""
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise ValueError("not JSON") from exc
    if not isinstance(payload, dict) or payload.get("code") != 0:
        message = payload.get("message") if isinstance(payload, dict) else None
        raise ValueError(f"Stove API error {message!r}")
    value = payload.get("value")
    if not isinstance(value, dict) or not isinstance(value.get("guide_list"), list):
        raise ValueError("no guide_list in the response")


def _inconsistent(pages: list[tuple[_Page, FetchResult]]) -> bool:
    if len(pages) < 2:
        return False
    totals = {p.total_count for p, _ in pages}
    times = [r.fetched_at for _, r in pages]
    return len(totals) > 1 or max(times) - min(times) > _CONSISTENCY_WINDOW


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
    if not items:
        detail = f" ({rejected[0]})" if rejected else f" (total_count={total})"
        raise StoveError(f"{endpoint}: no usable item{detail}")
    if rejected:
        sample = "; ".join(rejected[:5])
        warnings.append(f"stove {endpoint}: {len(rejected)} item(s) skipped (unexpected schema): {sample}")
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


def to_facts(
    catalog: StoveCatalog, artifact_stat_hints: Mapping[str, Mapping[str, float]] | None = None
) -> tuple[list[Fact], SourceRun]:
    """`artifact_stat_hints`: artifact code -> {"atk", "def", "hp"} values at +0 from another source (Fribbels), used
    to tell which two stats Stove's positional fields hold. Without a matching hint the stats are `assumed`."""
    facts: list[Fact] = []
    warnings = list(catalog.warnings)
    unconfirmed_stats: list[str] = []
    unknown_effects: list[str] = []

    def add(
        entity_type: EntityType,
        entity_id: str,
        name: str,
        value: JsonValue,
        note: str = "",
        status: DataStatus = DataStatus.VERIFIED,
    ) -> None:
        facts.append(
            Fact(
                entity_type=entity_type,
                entity_id=entity_id,
                field=name,
                value=value,
                source=SourceId.STOVE,
                status=status,
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
        facts.extend(set_facts(item.equip_code, item.equip_effect))

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

        stats, confirmed = _artifact_stat_names(art, (artifact_stat_hints or {}).get(code))
        status = DataStatus.VERIFIED if confirmed else DataStatus.ASSUMED
        note = "slot order confirmed by Fribbels +0 values" if confirmed else "slot-to-stat mapping not confirmed"
        if not confirmed:
            unconfirmed_stats.append(code)
        first, second = stats
        add(EntityType.ARTIFACT, code, f"{first}_min", art.ability_attack, note, status)
        add(EntityType.ARTIFACT, code, f"{first}_max", art.enhance_ability_attack, note, status)
        add(EntityType.ARTIFACT, code, f"{second}_min", art.ability_defense, note, status)
        add(EntityType.ARTIFACT, code, f"{second}_max", art.enhance_ability_defense, note, status)

        if art.info_text:
            add(EntityType.ARTIFACT, code, "effect_text", art.info_text.strip())
            levels, complete = _effect_levels(art)
            if levels:
                add(
                    EntityType.ARTIFACT,
                    code,
                    "effect_levels",
                    levels,
                    note="one entry per '@' placeholder (@, @@, ...): value at +0 and at max; null = unknown",
                    status=DataStatus.VERIFIED if complete else DataStatus.ASSUMED,
                )
                if not complete:
                    unknown_effects.append(code)
        else:
            warnings.append(f"stove artifact {code}: no effect text in the API response")

    if unconfirmed_stats:
        warnings.append(
            f"stove: {len(unconfirmed_stats)} artifact(s) whose stat slots are not confirmed by Fribbels "
            f"(stats stored as assumed): {_sample(unconfirmed_stats)}"
        )
    if unknown_effects:
        warnings.append(
            f"stove: {len(unknown_effects)} artifact(s) with effect values missing for some '@' "
            f"(null in effect_levels): {_sample(unknown_effects)}"
        )
    stale_reasons = sorted({r.stale_reason for r in catalog.results if r.stale and r.stale_reason})
    warnings.extend(f"stove: stale cache served ({reason})" for reason in stale_reasons)
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


def _artifact_stat_names(art: StoveArtifact, hint: Mapping[str, float] | None) -> tuple[tuple[str, str], bool]:
    """Which stats Stove's two positional fields hold, and whether another source confirms it."""
    if hint is not None:
        nonzero = [name for name in ARTIFACT_STAT_ORDER if hint.get(name, 0) > 0]
        if len(nonzero) == 2 and [hint[n] for n in nonzero] == [art.ability_attack, art.ability_defense]:
            return (nonzero[0], nonzero[1]), True
    return ("atk", "hp"), False


def _effect_levels(art: StoveArtifact) -> tuple[list[JsonValue], bool]:
    """Keep one entry per placeholder present in the text; sentinel values become null (unknown)."""
    runs = [len(m) for m in re.findall(r"@+", art.info_text or "")]
    count = max(runs, default=0)
    levels: list[JsonValue] = []
    complete = True
    for index in range(count):
        entry = art.level_list[index] if index < len(art.level_list) else StoveArtifactLevel()
        low = None if (entry.lv01 or "").strip() in _SENTINELS else entry.lv01
        high = None if (entry.lv_max or "").strip() in _SENTINELS else entry.lv_max
        complete = complete and low is not None and high is not None
        levels.append({"min": low, "max": high})
    return levels, complete


def _sample(codes: list[str], limit: int = 12) -> str:
    more = f" (+{len(codes) - limit} more)" if len(codes) > limit else ""
    return ", ".join(codes[:limit]) + more
