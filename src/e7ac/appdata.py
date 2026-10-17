"""Shared, UI-free access to the app's local data: the database and the catalog's heroes.

The command line and the window both open the same database through here, so neither needs the other's helpers.
"""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from e7ac.catalog.facts import EntityType
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.catalog.store import current_snapshot, load_entities
from e7ac.paths import AppPaths
from e7ac.storage.db import open_database


def open_engine(paths: AppPaths) -> Engine:
    """The app's database, created and migrated when needed."""
    paths.ensure()
    return open_database(paths.database)


def catalog_heroes(session: Session) -> dict[str, ResolvedEntity] | None:
    """Every hero of the current catalog by code; None when there is no catalog yet (callers must say so instead of
    silently filtering everything out)."""
    snapshot = current_snapshot(session)
    if snapshot is None:
        return None
    return {e.entity_id: e for e in load_entities(session, snapshot.id, EntityType.HERO)}
