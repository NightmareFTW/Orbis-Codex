"""Persist and query catalog snapshots (versioned: predictions will reference the snapshot they used)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from e7ac.catalog.facts import EntityType, Fact, SourceRun
from e7ac.catalog.resolve import ResolvedEntity, conflicts
from e7ac.domain.world import World
from e7ac.storage.models import CatalogEntityRow, CatalogFactRow, CatalogSnapshotRow

_RUNS_ADAPTER = TypeAdapter(list[SourceRun])


@dataclass(frozen=True, slots=True)
class SaveResult:
    snapshot_id: int
    created: bool
    """False when an identical snapshot already existed (it is made current again)."""


def content_hash(world: World, entities: Sequence[ResolvedEntity]) -> str:
    payload = json.dumps(
        {"world": world.value, "entities": [e.model_dump(mode="json") for e in entities]},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def save_snapshot(
    session: Session,
    *,
    world: World,
    entities: Sequence[ResolvedEntity],
    facts: Sequence[Fact],
    runs: Sequence[SourceRun],
    now: datetime,
    make_current: bool = True,
) -> SaveResult:
    """Store a snapshot (or find the identical one). Only `make_current` moves the 'current' marker, so a partial
    sync can be kept for inspection without replacing the last complete catalog."""
    digest = content_hash(world, entities)

    def find_existing() -> CatalogSnapshotRow | None:
        return session.scalars(select(CatalogSnapshotRow).where(CatalogSnapshotRow.content_sha256 == digest)).first()

    def promote(row: CatalogSnapshotRow) -> SaveResult:
        if make_current:
            session.execute(update(CatalogSnapshotRow).where(CatalogSnapshotRow.id != row.id).values(is_current=False))
            row.is_current = True
            session.flush()
        return SaveResult(snapshot_id=row.id, created=False)

    existing = find_existing()
    if existing is not None:
        return promote(existing)

    snapshot = CatalogSnapshotRow(
        created_at=now,
        content_sha256=digest,
        world=world.value,
        sources_json=_RUNS_ADAPTER.dump_json(list(runs)).decode("utf-8"),
        entity_count=len(entities),
        conflict_count=len(conflicts(entities)),
        is_current=False,
    )
    try:
        with session.begin_nested():
            session.add(snapshot)
            session.flush()
    except IntegrityError:
        # another process stored the same content meanwhile (unique content hash): reuse it
        raced = find_existing()
        if raced is None:
            raise
        return promote(raced)
    session.add_all(
        CatalogFactRow(
            snapshot_id=snapshot.id,
            entity_type=f.entity_type.value,
            entity_id=f.entity_id,
            field=f.field,
            value_json=json.dumps(f.value, sort_keys=True),
            source=f.source.value,
            status=f.status.value,
            note=f.note,
        )
        for f in facts
    )
    session.add_all(
        CatalogEntityRow(
            snapshot_id=snapshot.id,
            entity_type=e.entity_type.value,
            entity_id=e.entity_id,
            name=e.name[:128],
            data_json=e.model_dump_json(),
        )
        for e in entities
    )
    session.flush()
    if make_current:
        promote(snapshot)
    return SaveResult(snapshot_id=snapshot.id, created=True)


def current_snapshot(session: Session) -> CatalogSnapshotRow | None:
    return session.scalars(select(CatalogSnapshotRow).where(CatalogSnapshotRow.is_current.is_(True))).first()


def list_snapshots(session: Session) -> list[CatalogSnapshotRow]:
    return list(session.scalars(select(CatalogSnapshotRow).order_by(CatalogSnapshotRow.id.desc())))


def snapshot_runs(snapshot: CatalogSnapshotRow) -> list[SourceRun]:
    return _RUNS_ADAPTER.validate_json(snapshot.sources_json)


def load_entities(session: Session, snapshot_id: int, entity_type: EntityType | None = None) -> list[ResolvedEntity]:
    query = select(CatalogEntityRow).where(CatalogEntityRow.snapshot_id == snapshot_id)
    if entity_type is not None:
        query = query.where(CatalogEntityRow.entity_type == entity_type.value)
    query = query.order_by(CatalogEntityRow.entity_type, CatalogEntityRow.entity_id)
    return [ResolvedEntity.model_validate_json(row.data_json) for row in session.scalars(query)]


def load_entity(session: Session, snapshot_id: int, entity_type: EntityType, entity_id: str) -> ResolvedEntity | None:
    row = session.get(CatalogEntityRow, (snapshot_id, entity_type.value, entity_id))
    return None if row is None else ResolvedEntity.model_validate_json(row.data_json)


def load_facts(session: Session, snapshot_id: int, entity_type: EntityType, entity_id: str) -> list[CatalogFactRow]:
    query = (
        select(CatalogFactRow)
        .where(
            CatalogFactRow.snapshot_id == snapshot_id,
            CatalogFactRow.entity_type == entity_type.value,
            CatalogFactRow.entity_id == entity_id,
        )
        .order_by(CatalogFactRow.field, CatalogFactRow.source)
    )
    return list(session.scalars(query))


@dataclass(frozen=True, slots=True)
class Lookup:
    exact: list[ResolvedEntity]
    partial: list[ResolvedEntity]


def find(session: Session, snapshot_id: int, query: str) -> Lookup:
    """Exact code or exact (case-insensitive) name matches first; substring matches are only *candidates*.

    Callers must never treat a partial match as an answer (golden rule: no silent near-miss)."""
    needle = _fold(query)
    rows = session.scalars(
        select(CatalogEntityRow).where(
            CatalogEntityRow.snapshot_id == snapshot_id,
            CatalogEntityRow.entity_type != EntityType.SKILL.value,
        )
    ).all()
    exact = [r for r in rows if r.entity_id.casefold() == needle or _fold(r.name) == needle]
    partial = [r for r in rows if r not in exact and needle and needle in _fold(r.name)]
    return Lookup(
        exact=[ResolvedEntity.model_validate_json(r.data_json) for r in exact],
        partial=[ResolvedEntity.model_validate_json(r.data_json) for r in sorted(partial, key=lambda r: r.name)],
    )


def _fold(text: str) -> str:
    """Case- and quote-insensitive form: typographic quotes (U+2018/U+2019) are the same character to a user."""
    return text.strip().casefold().replace("\u2019", "'").replace("\u2018", "'")
