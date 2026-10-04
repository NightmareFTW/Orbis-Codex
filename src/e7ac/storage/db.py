"""Database access: one SQLite file in the app home, schema kept at Alembic head."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # URL.create: no percent-decoding or '?' query parsing of the path (paths may contain '%', '?', '#')
    engine = create_engine(URL.create("sqlite", database=str(db_path)))

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
        # pysqlite's own transaction handling only BEGINs before DML, so a SAVEPOINT issued first would open (and
        # its RELEASE commit) the outer transaction. Let SQLAlchemy emit BEGIN itself (documented pysqlite recipe).
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection: Any) -> None:
        connection.exec_driver_sql("BEGIN")

    return engine


def alembic_config(engine: Engine) -> Config:
    config = Config()
    # ConfigParser interpolation: a '%' in the install path must be escaped
    config.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    config.attributes["engine"] = engine
    return config


def upgrade_to_head(engine: Engine, attempts: int = 3) -> None:
    """Migrate to the latest revision. Retried when another e7 process is migrating the same file concurrently."""
    for attempt in range(attempts):
        try:
            command.upgrade(alembic_config(engine), "head")
            return
        except OperationalError as exc:
            if attempt == attempts - 1 or not _concurrent_migration(exc):
                raise
            time.sleep(0.5 * (attempt + 1))


def _concurrent_migration(exc: OperationalError) -> bool:
    message = str(exc).lower()
    return "already exists" in message or "database is locked" in message


def open_database(db_path: Path) -> Engine:
    """Engine with the schema migrated to the latest revision."""
    engine = make_engine(db_path)
    upgrade_to_head(engine)
    return engine


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Commit on success, roll back on error."""
    factory = sessionmaker(engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
