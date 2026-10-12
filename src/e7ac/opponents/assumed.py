"""An assumed opponent build from Stove usage statistics: one percentile of each stat (M10).

Profiles: typical = P50, strong = P75, top = P90 of each stat, **taken separately**: real builds trade stats against
each other, so a profile that is high in every stat at once is optimistic (the sampler of a later slice keeps that
in mind). Everything here is `assumed` (bin edges NV-01, a population with PvE builds mixed in), so the confidence
stays below what the user's own roster gets from the screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from e7ac.domain.codes import Stat
from e7ac.sources.stove_usage import Choice, HeroUsage


class Profile(StrEnum):
    TYPICAL = "typical"
    STRONG = "strong"
    TOP = "top"


QUANTILES: Final = {Profile.TYPICAL: 0.5, Profile.STRONG: 0.75, Profile.TOP: 0.9}
CONFIDENCE: Final = 0.5
"""A percentile of the population, with assumed bin edges: a fair guess, never a reading."""
OPEN_END_CONFIDENCE: Final = 0.3
"""The percentile is in an open end bin: the real value may lie beyond Stove's chart range."""
SMALL_SAMPLE: Final = 200
STALE_AFTER: Final = timedelta(days=8)
"""Stove refreshes weekly: older data missed a refresh."""


@dataclass(frozen=True, slots=True)
class AssumedStat:
    value: float
    open_end: bool
    confidence: float


@dataclass(slots=True)
class AssumedBuild:
    hero_code: str
    hero_name: str
    profile: Profile
    stats: dict[Stat, AssumedStat]
    sets: list[Choice]
    ee: list[Choice]
    artifacts: list[Choice]
    sample_size: int | None
    updated: str
    warnings: list[str] = field(default_factory=list)


def assumed_build(usage: HeroUsage, profile: Profile, now: datetime) -> AssumedBuild:
    q = QUANTILES[profile]
    small = usage.sample_size is not None and usage.sample_size < SMALL_SAMPLE
    stats: dict[Stat, AssumedStat] = {}
    for stat, histogram in usage.histograms.items():
        found = histogram.percentile(q)
        if found is None:
            continue
        confidence = OPEN_END_CONFIDENCE if found.open_end or small else CONFIDENCE
        stats[stat] = AssumedStat(found.value, found.open_end, confidence)
    build = AssumedBuild(
        hero_code=usage.hero_code,
        hero_name=usage.hero_name,
        profile=profile,
        stats=stats,
        sets=usage.sets,
        ee=usage.ee,
        artifacts=usage.artifacts,
        sample_size=usage.sample_size,
        updated=usage.updated,
        warnings=list(usage.warnings),
    )
    if small:
        build.warnings.append(f"only {usage.sample_size} builds behind these statistics: rough")
    updated = _date(usage.updated)
    if updated is not None and now.replace(tzinfo=None) - updated > STALE_AFTER:
        build.warnings.append(f"Stove data from {usage.updated[:10]}: it missed a weekly refresh")
    missing = [s.value for s, h in usage.histograms.items() if h.total == 0]
    if missing:
        build.warnings.append(f"no data for {', '.join(missing)}")
    return build


def _date(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text[:19])
    except ValueError:
        return None
