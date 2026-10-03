"""`e7 catalog ...` commands."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated, Final

import typer
from pydantic import JsonValue
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from e7ac.catalog.coverage import coverage, status_counts
from e7ac.catalog.facts import EntityType
from e7ac.catalog.resolve import ResolvedEntity, conflicts
from e7ac.catalog.store import (
    current_snapshot,
    find,
    list_snapshots,
    load_entities,
    load_entity,
    snapshot_runs,
)
from e7ac.catalog.sync import ALL_SOURCES, SyncError, SyncOptions, sync_catalog
from e7ac.domain.codes import DataStatus, SourceId
from e7ac.domain.world import World
from e7ac.paths import AppPaths, default_paths
from e7ac.settings import SettingsError, load_settings
from e7ac.sources.http import CachedHttp
from e7ac.storage.db import open_database, session_scope
from e7ac.storage.models import CatalogSnapshotRow

catalog_app = typer.Typer(help="Game catalog: heroes, skills, artifacts, sets (with sources and status).")

_VALUE_WIDTH: Final = 48
_PLURAL: Final = {
    EntityType.HERO: "heroes",
    EntityType.SKILL: "skills",
    EntityType.ARTIFACT: "artifacts",
    EntityType.SET: "sets",
}


def make_http(paths: AppPaths, offline: bool) -> CachedHttp:
    """Factory used by the commands (tests replace it to inject a mock transport)."""
    return CachedHttp(cache_dir=paths.cache_dir, offline=offline)


http_factory: Callable[[AppPaths, bool], CachedHttp] = make_http


def _engine(paths: AppPaths) -> Engine:
    paths.ensure()
    return open_database(paths.database)


def _world_from_settings(paths: AppPaths) -> World:
    try:
        return load_settings(paths.settings_file).world
    except SettingsError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@catalog_app.command("sync")
def sync(
    source: Annotated[
        list[SourceId] | None,
        typer.Option("--source", "-s", help="Only these sources (repeatable). Default: stove, fribbels, e7calc."),
    ] = None,
    world: Annotated[World | None, typer.Option(help="Region for Stove data (default: from settings).")] = None,
    refresh: Annotated[bool, typer.Option("--refresh", help="Ignore cache freshness and re-download.")] = False,
    offline: Annotated[bool, typer.Option("--offline", help="Use cached downloads only (no network).")] = False,
) -> None:
    """Download (politely, cached) and merge the catalog sources into a new versioned snapshot."""
    paths = default_paths()
    chosen = frozenset(source) if source else ALL_SOURCES
    unsupported = chosen - ALL_SOURCES
    if unsupported:
        typer.echo(f"Error: not a catalog source: {', '.join(sorted(s.value for s in unsupported))}", err=True)
        raise typer.Exit(code=2)
    options = SyncOptions(world=world or _world_from_settings(paths), sources=chosen, refresh=refresh)
    engine = _engine(paths)
    with http_factory(paths, offline) as http:
        try:
            report = sync_catalog(engine, http, options)
        except SyncError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=2) from exc

    typer.echo(f"Catalog sync ({options.world.value}) - {report.requests_made} network request(s)")
    for run in report.runs:
        flags = (" [cache]" if run.from_cache else "") + (" [STALE: network unavailable]" if run.stale else "")
        fetched = run.retrieved_at.strftime("%Y-%m-%d %H:%M UTC")
        typer.echo(f"  {run.source.value:<9} {run.facts:>6} facts  fetched {fetched}  {run.version}{flags}")
    counts = ", ".join(f"{n} {_PLURAL[t]}" for t, n in sorted(report.entity_counts.items()))
    typer.echo(f"Snapshot #{report.snapshot_id} ({'new' if report.created else 'unchanged'}): {counts}")
    typer.echo(f"Conflicts between sources: {len(report.conflicts)}   (e7 catalog conflicts)")
    typer.echo(f"Entities with missing/assumed required fields: {len(report.gaps)}   (e7 catalog coverage)")
    warnings = [w for run in report.runs for w in run.warnings]
    for warning in warnings:
        typer.echo(f"  warning: {warning}")
    for error in report.errors:
        typer.echo(f"  ERROR: {error}", err=True)
    if report.errors:
        raise typer.Exit(code=1)


@catalog_app.command("show")
def show(
    query: Annotated[str, typer.Argument(help="Code (c2011, efa22, set_cri) or exact name.")],
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON with every field's status and sources.")] = False,
) -> None:
    """Show one hero / artifact / set with the value, status and sources of every field."""
    engine = _engine(default_paths())
    with session_scope(engine) as session:
        snapshot = _require_snapshot(session)
        lookup = find(session, snapshot.id, query)
        if not lookup.exact:
            if lookup.partial:
                typer.echo(f"No exact match for {query!r}. Candidates (use the code or the exact name):", err=True)
                for entity in lookup.partial[:15]:
                    typer.echo(f"  {entity.entity_id:<10} {entity.entity_type.value:<8} {entity.name}", err=True)
            else:
                typer.echo(f"Nothing found for {query!r}.", err=True)
            raise typer.Exit(code=1)
        if len(lookup.exact) > 1:
            typer.echo(f"{query!r} matches several entries; use the code:", err=True)
            for entity in lookup.exact:
                typer.echo(f"  {entity.entity_id:<10} {entity.entity_type.value:<8} {entity.name}", err=True)
            raise typer.Exit(code=1)
        entity = lookup.exact[0]
        skills = []
        if entity.entity_type is EntityType.HERO:
            for slot in (1, 2, 3):
                skill = load_entity(session, snapshot.id, EntityType.SKILL, f"{entity.entity_id}:s{slot}")
                if skill is not None:
                    skills.append(skill)
    if as_json:
        payload = {
            "snapshot": snapshot.id,
            "entity": entity.model_dump(mode="json"),
            "skills": [s.model_dump(mode="json") for s in skills],
        }
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    typer.echo(f"{entity.name} ({entity.entity_id}) - {entity.entity_type.value} - snapshot #{snapshot.id}")
    _print_fields(entity)
    for skill in skills:
        typer.echo(f"\nSkill {skill.entity_id.split(':')[1].upper()}")
        _print_fields(skill)


@catalog_app.command("conflicts")
def show_conflicts(
    entity_type: Annotated[EntityType | None, typer.Option("--type", help="Only this entity type.")] = None,
) -> None:
    """List fields where sources disagree (the chosen value, its status, and the alternatives)."""
    engine = _engine(default_paths())
    with session_scope(engine) as session:
        snapshot = _require_snapshot(session)
        entities = load_entities(session, snapshot.id, entity_type)
    names = {e.entity_id: e.name for e in entities}
    found = conflicts(entities)
    for conflict in found:
        label = names.get(conflict.entity_id.split(":")[0], "")
        alternatives = "; ".join(
            f"{_fmt(a.value)} ({', '.join(s.value for s in a.sources)})" for a in conflict.alternatives
        )
        typer.echo(
            f"{conflict.entity_id:<10} {label[:24]:<24} {conflict.field:<16} "
            f"{_fmt(conflict.chosen.value)} ({', '.join(s.value for s in conflict.chosen.sources)}) "
            f"[{conflict.status.value}]  vs  {alternatives}"
        )
    typer.echo(f"{len(found)} conflict(s) in snapshot #{snapshot.id}")


@catalog_app.command("coverage")
def show_coverage(
    entity_type: Annotated[EntityType | None, typer.Option("--type", help="Only this entity type.")] = None,
) -> None:
    """List entities that miss required fields or only have `assumed` values for them."""
    engine = _engine(default_paths())
    with session_scope(engine) as session:
        snapshot = _require_snapshot(session)
        entities = load_entities(session, snapshot.id, entity_type)
    gaps = coverage(entities)
    for gap in gaps:
        parts = []
        if gap.missing:
            parts.append("missing: " + ", ".join(gap.missing))
        if gap.assumed:
            parts.append("assumed: " + ", ".join(gap.assumed))
        typer.echo(f"{gap.entity_id:<10} {gap.entity_type.value:<8} {gap.name[:28]:<28} {'; '.join(parts)}")
    counts = status_counts(entities)
    summary = "  ".join(f"{status.value}={counts[status]}" for status in DataStatus)
    typer.echo(f"{len(gaps)} entit(ies) with gaps in snapshot #{snapshot.id}. Field status: {summary}")


@catalog_app.command("snapshots")
def snapshots() -> None:
    """List stored catalog snapshots (the current one is marked with *)."""
    engine = _engine(default_paths())
    with session_scope(engine) as session:
        rows = list_snapshots(session)
        if not rows:
            typer.echo("No catalog snapshot yet. Run: e7 catalog sync")
            return
        for row in rows:
            versions = ", ".join(f"{r.source.value}={r.version}" for r in snapshot_runs(row))
            mark = "*" if row.is_current else " "
            created = row.created_at.strftime("%Y-%m-%d %H:%M UTC")
            typer.echo(
                f"{mark} #{row.id:<4} {created}  {row.world:<12} "
                f"{row.entity_count} entities, {row.conflict_count} conflicts  [{versions}]"
            )


def _require_snapshot(session: Session) -> CatalogSnapshotRow:
    snapshot = current_snapshot(session)
    if snapshot is None:
        typer.echo("No catalog yet. Run: e7 catalog sync", err=True)
        raise typer.Exit(code=2)
    return snapshot


def _print_fields(entity: ResolvedEntity) -> None:
    for name, resolved in entity.fields.items():
        sources = ", ".join(s.value for s in resolved.sources)
        typer.echo(f"  {name:<18} {_fmt(resolved.value):<{_VALUE_WIDTH}} {resolved.status.value:<9} {sources}")
        for alternative in resolved.alternatives:
            alt_sources = ", ".join(s.value for s in alternative.sources)
            typer.echo(f"  {'':<18}   conflict: {_fmt(alternative.value)} ({alt_sources})")


def _fmt(value: JsonValue) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= _VALUE_WIDTH else text[: _VALUE_WIDTH - 3] + "..."
