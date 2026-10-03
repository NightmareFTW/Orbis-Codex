"""`e7 catalog sync`: fetch every source, turn it into facts, resolve, store a versioned snapshot."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import Engine

from e7ac.catalog.coverage import Gap, coverage
from e7ac.catalog.facts import EntityType, Fact, SourceRun
from e7ac.catalog.names import NameIndex
from e7ac.catalog.resolve import Conflict, conflicts, resolve
from e7ac.catalog.sets import set_facts
from e7ac.catalog.store import save_snapshot
from e7ac.domain.codes import SourceId
from e7ac.domain.world import World
from e7ac.sources import e7calc, fribbels, stove
from e7ac.sources.http import CachedHttp, FetchError
from e7ac.storage.db import session_scope

ALL_SOURCES: Final = frozenset({SourceId.STOVE, SourceId.FRIBBELS, SourceId.E7CALC})


class SyncError(Exception):
    """No source produced any data."""


@dataclass(frozen=True, slots=True)
class SyncOptions:
    world: World = World.GLOBAL
    sources: frozenset[SourceId] = ALL_SOURCES
    refresh: bool = False


@dataclass(slots=True)
class SyncReport:
    runs: list[SourceRun] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    snapshot_id: int | None = None
    created: bool = False
    entity_counts: dict[EntityType, int] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    requests_made: int = 0


def collect_facts(http: CachedHttp, options: SyncOptions) -> tuple[list[Fact], list[SourceRun], list[str]]:
    facts: list[Fact] = []
    runs: list[SourceRun] = []
    errors: list[str] = []
    names = NameIndex()
    known: dict[str, tuple[str, str]] = {}

    if SourceId.STOVE in options.sources:
        try:
            catalog = stove.fetch_catalog(http, options.world, refresh=options.refresh)
            stove_facts, run = stove.to_facts(catalog)
            for item in catalog.sets:
                stove_facts.extend(set_facts(item.equip_code, item.equip_effect))
            facts.extend(stove_facts)
            runs.append(run.model_copy(update={"facts": len(stove_facts)}))
            for hero in catalog.heroes:
                names.add(hero.hero_name, hero.hero_code)
                known[hero.hero_code] = (hero.attribute_code.value, hero.job_code.value)
        except (FetchError, stove.StoveError) as exc:
            errors.append(f"stove: {exc}")

    if SourceId.FRIBBELS in options.sources or SourceId.E7CALC in options.sources:
        try:
            data = fribbels.fetch(http, refresh=options.refresh)
            if SourceId.FRIBBELS in options.sources:
                fribbels_facts, run = fribbels.to_facts(data)
                facts.extend(fribbels_facts)
                runs.append(run)
            fribbels.add_to_index(data, names)
            for fhero in data.heroes:
                known.setdefault(fhero.code, (fhero.attribute.value, fhero.role.value))
        except (FetchError, fribbels.FribbelsError, ValueError) as exc:
            errors.append(f"fribbels: {exc}")

    if SourceId.E7CALC in options.sources:
        if not len(names):
            errors.append("e7calc: skipped, no hero name index (needs Stove or Fribbels data to map names to codes)")
        else:
            try:
                calc = e7calc.fetch(http, refresh=options.refresh)
                calc_facts, run = e7calc.to_facts(calc, names, known)
                facts.extend(calc_facts)
                runs.append(run)
            except (FetchError, ValueError) as exc:
                errors.append(f"e7calc: {exc}")
    ambiguous = names.ambiguous()
    if ambiguous:
        listed = "; ".join(f"{key}: {', '.join(codes)}" for key, codes in ambiguous.items())
        notice = f"names shared by several hero codes (never matched by name): {listed}"
        target = next((i for i, r in enumerate(runs) if r.source is SourceId.STOVE), 0)
        if runs:
            runs[target] = runs[target].model_copy(update={"warnings": (*runs[target].warnings, notice)})
    return facts, runs, errors


def sync_catalog(
    engine: Engine,
    http: CachedHttp,
    options: SyncOptions,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SyncReport:
    facts, runs, errors = collect_facts(http, options)
    report = SyncReport(runs=runs, errors=errors, requests_made=http.requests_made)
    if not facts:
        raise SyncError("no source produced data: " + "; ".join(errors or ["no source selected"]))
    entities = resolve(facts)
    with session_scope(engine) as session:
        saved = save_snapshot(session, world=options.world, entities=entities, facts=facts, runs=runs, now=now())
    report.snapshot_id = saved.snapshot_id
    report.created = saved.created
    report.entity_counts = dict(Counter(e.entity_type for e in entities))
    report.conflicts = conflicts(entities)
    report.gaps = coverage(entities)
    return report
