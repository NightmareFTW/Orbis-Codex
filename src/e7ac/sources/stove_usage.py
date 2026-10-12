"""Stove per-hero usage statistics (`hero-detail-for-game`): stat histograms and the most used sets, EE options and
artifacts of a hero, for the opponent model (M10, docs/DATA_SOURCES.md §1).

The population is "Lv. 60 / 6★ awakened / fully equipped" hero instances of the past 7 days in one region, all
content mixed (not Arena only). Stove refreshes it weekly (Thursday 03:00 UTC, `assumed`): a cached copy older than
the last refresh is fetched again, so a hero costs at most one polite request per week.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from e7ac.domain.codes import Stat
from e7ac.domain.world import World
from e7ac.opponents.histogram import StatHistogram, parse_counts, sample_size
from e7ac.sources.http import CachedHttp

DETAIL_URL: Final = "https://api.onstove.com/pub-meta/v1.0/epic7/guide/hunt/hero-detail-for-game"
STAT_FIELDS: Final[Mapping[str, Stat]] = {
    "attack_stats": Stat.ATK,
    "defense_stats": Stat.DEF,
    "vitality_statistics": Stat.HP,
    "speed_statistics": Stat.SPEED,
    "critical_statistics": Stat.CRIT_CHANCE,
    "critical_hit_statistics": Stat.CRIT_DAMAGE,
    "effective_statistics": Stat.EFFECTIVENESS,
    "effect_resistance_statistics": Stat.EFFECT_RESISTANCE,
}
WEEKLY_UPDATE: Final = (3, 3)
"""Stove's weekly refresh: (weekday, hour UTC) = Thursday 03:00 (`assumed`, DATA_SOURCES §1)."""


class UsageError(Exception):
    """The response is not usable usage data."""


@dataclass(frozen=True, slots=True)
class Choice:
    """One of the top-3 choices Stove lists, with its share of the population (0-1)."""

    codes: tuple[str, ...]
    """Set codes of a set combination ('set_cri_dmg', ...), or one EE / artifact code."""
    share: float
    name: str = ""
    skill: int | None = None
    """EE only: the skill (1-3) the option modifies."""


@dataclass(slots=True)
class HeroUsage:
    hero_code: str
    hero_name: str
    world: World
    updated: str
    """Stove's `reg_date` as sent (its time zone is not documented)."""
    sample_size: int | None
    """Hero instances behind the histograms; None when the 8 totals disagree (then each has its own)."""
    histograms: dict[Stat, StatHistogram]
    sets: list[Choice]
    ee: list[Choice]
    artifacts: list[Choice]
    fetched_at: datetime
    stale: bool = False
    """An expired cached copy was used (offline or the request failed)."""
    warnings: list[str] = field(default_factory=list)


def last_weekly_update(now: datetime) -> datetime:
    """The most recent Stove refresh at or before `now` (UTC)."""
    weekday, hour = WEEKLY_UPDATE
    start = now.astimezone(UTC).replace(hour=hour, minute=0, second=0, microsecond=0)
    start -= timedelta(days=(start.weekday() - weekday) % 7)
    return start if start <= now else start - timedelta(days=7)


def fetch_hero_usage(http: CachedHttp, world: World, hero_code: str, *, refresh: bool = False) -> HeroUsage | None:
    """The hero's usage statistics, or None when Stove has none for it (`seq == 0`, "Gathering Hero Information")."""
    now = http.now()
    result = http.get_text(
        f"stove/{world.value}/usage",
        DETAIL_URL,
        {
            "world_code": world.value,
            "stage": "1",
            "lang_code": "en",
            "hero_code": hero_code,
            "strategy_type": "0",
            "boss_type": "1",
        },
        max_age=max(now - last_weekly_update(now), timedelta(minutes=1)),
        refresh=refresh,
        validate=validate_detail,
    )
    usage = parse_detail(json.loads(result.text)["value"], world, result.fetched_at)
    if usage is not None:
        usage.stale = result.stale
        if result.stale:
            usage.warnings.append(f"Stove data from the cache, not refreshed ({result.stale_reason})")
    return usage


def validate_detail(text: str) -> None:
    """Accept only a successful envelope with a hero (before caching: error pages never replace good data)."""
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise ValueError("not JSON") from exc
    if not isinstance(payload, dict) or payload.get("code") != 0 or not isinstance(payload.get("value"), dict):
        raise ValueError(f"Stove API error {payload.get('message') if isinstance(payload, dict) else None!r}")


def parse_detail(value: Mapping[str, Any], world: World, fetched_at: datetime) -> HeroUsage | None:
    if not value.get("seq"):
        return None
    try:
        histograms = {stat: parse_counts(stat, str(value[name])) for name, stat in STAT_FIELDS.items()}
    except (KeyError, ValueError) as exc:
        raise UsageError(f"{value.get('hero_code')}: unusable histograms ({exc})") from exc
    usage = HeroUsage(
        hero_code=str(value["hero_code"]),
        hero_name=str(value.get("hero_name", "")),
        world=world,
        updated=str(value.get("reg_date", "")),
        sample_size=sample_size(list(histograms.values())),
        histograms=histograms,
        sets=[
            Choice(tuple(f"set_{c}" for c in str(e["rank_sets"]).split(",") if c), _share(e["eqip_rank_share"]))
            for e in value.get("hero_eqips") or []
            if e.get("rank_sets")
        ],
        ee=[
            Choice((str(e["rank_skill"]),), _share(e["skill_rank_share"]), skill=_int(e.get("enhancemen_skill")))
            for e in value.get("hero_dedicated_eqip") or []
            if e.get("rank_skill") and e.get("skill_rank_share")
        ],
        artifacts=[
            Choice((str(e["rank_arti"]),), _share(e["arti_rank_share"]), name=str(e.get("rank_arti_name", "")))
            for e in value.get("hero_arti") or []
            if e.get("rank_arti")
        ],
        fetched_at=fetched_at,
    )
    if usage.sample_size is None:
        usage.warnings.append("the stat histograms have different totals (unexpected: each is used on its own)")
    return usage


def _share(percent: object) -> float:
    return round(float(str(percent)) / 100, 6)


def _int(text: object) -> int | None:
    try:
        return int(str(text))
    except ValueError:
        return None
