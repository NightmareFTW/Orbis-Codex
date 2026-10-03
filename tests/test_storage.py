from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select
from sqlalchemy.exc import StatementError

from e7ac.storage.db import open_database, session_scope
from e7ac.storage.models import Base, CatalogSnapshotRow


def test_migrations_create_the_schema_and_match_the_models(tmp_path: Path) -> None:
    engine = open_database(tmp_path / "db.sqlite3")
    assert {"catalog_snapshot", "catalog_fact", "catalog_entity", "alembic_version"} <= set(
        inspect(engine).get_table_names()
    )
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == [], f"models and migrations differ - add an Alembic migration: {diff}"


def test_open_database_is_idempotent(tmp_path: Path) -> None:
    open_database(tmp_path / "db.sqlite3").dispose()
    open_database(tmp_path / "db.sqlite3").dispose()


def _snapshot(created_at: datetime, digest: str = "a" * 64) -> CatalogSnapshotRow:
    return CatalogSnapshotRow(
        created_at=created_at,
        content_sha256=digest,
        world="world_global",
        sources_json="[]",
        entity_count=0,
        conflict_count=0,
        is_current=True,
    )


def test_datetimes_round_trip_as_utc(tmp_path: Path) -> None:
    engine = open_database(tmp_path / "db.sqlite3")
    lisbon_summer = timezone(timedelta(hours=1))
    with session_scope(engine) as session:
        session.add(_snapshot(datetime(2026, 10, 3, 17, 30, tzinfo=lisbon_summer)))
    with session_scope(engine) as session:
        stored = session.scalars(select(CatalogSnapshotRow)).one().created_at
    assert stored == datetime(2026, 10, 3, 16, 30, tzinfo=UTC)
    assert stored.tzinfo is UTC


def test_naive_datetimes_are_rejected(tmp_path: Path) -> None:
    engine = open_database(tmp_path / "db.sqlite3")
    with pytest.raises(StatementError, match="naive datetimes"), session_scope(engine) as session:
        session.add(_snapshot(datetime(2026, 10, 3, 12, 0)))
        session.flush()
