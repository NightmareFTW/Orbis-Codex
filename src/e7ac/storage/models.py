"""ORM tables. Schema changes need an Alembic migration (tests/test_storage.py checks models == migrations)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, TypeDecorator
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on SQLite (which has no timezone support)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not allowed; use timezone-aware UTC")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class CatalogSnapshotRow(Base):
    __tablename__ = "catalog_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    world: Mapped[str] = mapped_column(String(32), nullable=False)
    sources_json: Mapped[str] = mapped_column(Text, nullable=False)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False)
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CatalogFactRow(Base):
    __tablename__ = "catalog_fact"
    __table_args__ = (Index("ix_catalog_fact_entity", "snapshot_id", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("catalog_snapshot.id", ondelete="CASCADE"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")


class CatalogEntityRow(Base):
    __tablename__ = "catalog_entity"

    snapshot_id: Mapped[int] = mapped_column(ForeignKey("catalog_snapshot.id", ondelete="CASCADE"), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
