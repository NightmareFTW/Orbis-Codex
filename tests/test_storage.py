from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import event, func, insert, inspect, select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import ORMExecuteState, Session

from e7ac.catalog.facts import EntityType, Fact
from e7ac.catalog.resolve import resolve
from e7ac.catalog.store import content_hash, current_snapshot, save_snapshot
from e7ac.domain.codes import DataStatus, SourceId
from e7ac.domain.world import World
from e7ac.storage.db import open_database, session_scope
from e7ac.storage.models import Base, CatalogFactRow, CatalogSnapshotRow


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


@pytest.mark.parametrize(
    "folder",
    [
        "100% sure #1",
        pytest.param("what?", marks=pytest.mark.skipif(os.name == "nt", reason="'?' is not allowed on Windows")),
        "%(here)s",
    ],
)
def test_database_path_may_contain_url_and_ini_special_characters(tmp_path: Path, folder: str) -> None:
    db_path = tmp_path / folder / "db.sqlite3"
    engine = open_database(db_path)
    with session_scope(engine) as session:
        session.add(_snapshot(datetime(2026, 10, 3, tzinfo=UTC)))
    engine.dispose()
    assert db_path.is_file()  # the file we asked for, not a percent-decoded / truncated sibling
    assert {p.name for p in (tmp_path / folder).iterdir()} >= {"db.sqlite3"}


NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
FACTS = [
    Fact(
        entity_type=EntityType.HERO,
        entity_id="c9001",
        field="name",
        value="Test Hero",
        source=SourceId.STOVE,
        status=DataStatus.VERIFIED,
    )
]


def _save(session: Session, make_current: bool = True) -> int:
    saved = save_snapshot(
        session, world=World.GLOBAL, entities=resolve(FACTS), facts=FACTS, runs=[], now=NOW, make_current=make_current
    )
    return saved.snapshot_id


def test_failed_sync_leaves_no_half_written_snapshot(tmp_path: Path) -> None:
    """The snapshot row and its facts commit together or not at all (the savepoint must not commit early)."""
    engine = open_database(tmp_path / "db.sqlite3")
    with pytest.raises(RuntimeError), session_scope(engine) as session:
        _save(session)
        raise RuntimeError("crash after the snapshot row was flushed")
    with session_scope(engine) as session:
        assert session.scalar(select(func.count()).select_from(CatalogSnapshotRow)) == 0
        assert session.scalar(select(func.count()).select_from(CatalogFactRow)) == 0


def test_concurrent_identical_snapshot_is_reused(tmp_path: Path) -> None:
    """Another process stores the same content between our lookup and our insert: reuse its row, no error."""
    engine = open_database(tmp_path / "db.sqlite3")
    digest = content_hash(World.GLOBAL, resolve(FACTS))
    with session_scope(engine) as session:
        state = {"armed": True, "raced_id": 0}

        @event.listens_for(session, "do_orm_execute")
        def _race(execute_state: ORMExecuteState) -> object:
            if not (state["armed"] and execute_state.is_select):
                return None
            state["armed"] = False
            result = execute_state.invoke_statement().freeze()  # our lookup: nothing there yet
            row = {**_snapshot(NOW, digest).__dict__}
            row.pop("_sa_instance_state")
            row["is_current"] = False
            state["raced_id"] = (
                execute_state.session.connection()
                .execute(insert(CatalogSnapshotRow).values(row).returning(CatalogSnapshotRow.id))
                .scalar_one()
            )
            return result()

        snapshot_id = _save(session)
        assert snapshot_id == state["raced_id"] != 0
        current = current_snapshot(session)
        assert current is not None and current.id == snapshot_id
    with session_scope(engine) as session:
        assert session.scalar(select(func.count()).select_from(CatalogSnapshotRow)) == 1


def test_partial_snapshot_does_not_move_the_current_marker(tmp_path: Path) -> None:
    engine = open_database(tmp_path / "db.sqlite3")
    with session_scope(engine) as session:
        session.add(_snapshot(NOW))  # an older complete catalog, current
    with session_scope(engine) as session:
        partial_id = _save(session, make_current=False)
        current = current_snapshot(session)
        assert current is not None and current.id != partial_id
        assert _save(session, make_current=True) == partial_id  # same content later complete: promoted
        current = current_snapshot(session)
        assert current is not None and current.id == partial_id
