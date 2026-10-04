"""Alembic environment: the engine is passed in by e7ac.storage.db (no alembic.ini needed)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Engine

from e7ac.storage.models import Base

target_metadata = Base.metadata


def run_migrations() -> None:
    engine = context.config.attributes.get("engine")
    if not isinstance(engine, Engine):
        raise RuntimeError("run migrations through e7ac.storage.db.upgrade_to_head(engine)")
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
