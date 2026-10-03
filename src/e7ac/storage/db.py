"""Database access: one SQLite file in the app home, schema kept at Alembic head."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def alembic_config(engine: Engine) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["engine"] = engine
    return config


def upgrade_to_head(engine: Engine) -> None:
    command.upgrade(alembic_config(engine), "head")


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
