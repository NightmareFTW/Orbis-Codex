"""ORM tables. Schema changes need an Alembic migration (tests/test_storage.py checks models == migrations)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, TypeDecorator
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


# ---------------------------------------------------------------------------------------------------- roster (M3)


class OwnedHeroRow(Base):
    """One owned copy of a hero (a roster can hold several copies of the same hero code)."""

    __tablename__ = "owned_hero"
    __table_args__ = (Index("ix_owned_hero_hero_code", "hero_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    hero_code: Mapped[str] = mapped_column(String(16), nullable=False)
    arena_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class HeroSnapshotRow(Base):
    """Immutable build snapshot; editing a hero adds a new row and moves `is_current`."""

    __tablename__ = "hero_snapshot"
    __table_args__ = (Index("ix_hero_snapshot_owned_hero_id", "owned_hero_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    owned_hero_id: Mapped[int] = mapped_column(ForeignKey("owned_hero.id", ondelete="CASCADE"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stars: Mapped[int] = mapped_column(Integer, nullable=False)
    awakening: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    skill_s1: Mapped[int] = mapped_column(Integer, nullable=False)
    skill_s2: Mapped[int] = mapped_column(Integer, nullable=False)
    skill_s3: Mapped[int] = mapped_column(Integer, nullable=False)
    imprint_grade: Mapped[str | None] = mapped_column(String(4))
    imprint_stat: Mapped[str | None] = mapped_column(String(16))
    imprint_value: Mapped[float | None] = mapped_column(Float)
    ee_stat: Mapped[str | None] = mapped_column(String(16))
    ee_value: Mapped[float | None] = mapped_column(Float)
    ee_option_code: Mapped[str | None] = mapped_column(String(32))
    ee_option_text: Mapped[str | None] = mapped_column(Text)
    artifact_code: Mapped[str | None] = mapped_column(String(16))
    artifact_level: Mapped[int | None] = mapped_column(Integer)
    atk: Mapped[int | None] = mapped_column(Integer)
    defense: Mapped[int | None] = mapped_column(Integer)
    hp: Mapped[int | None] = mapped_column(Integer)
    speed: Mapped[int | None] = mapped_column(Integer)
    crit_chance: Mapped[float | None] = mapped_column(Float)
    crit_damage: Mapped[float | None] = mapped_column(Float)
    effectiveness: Mapped[float | None] = mapped_column(Float)
    effect_resistance: Mapped[float | None] = mapped_column(Float)
    dual_attack: Mapped[float | None] = mapped_column(Float)
    cp: Mapped[int | None] = mapped_column(Integer)
    confidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")


class GearRow(Base):
    """A gear piece as seen at some point (content-immutable; re-used across snapshots when identical)."""

    __tablename__ = "gear"
    __table_args__ = (
        Index("ix_gear_fingerprint", "fingerprint"),
        Index("ix_gear_external_id", "external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot: Mapped[str] = mapped_column(String(16), nullable=False)
    set_code: Mapped[str] = mapped_column(String(32), nullable=False)
    grade: Mapped[str] = mapped_column(String(16), nullable=False)
    item_level: Mapped[int] = mapped_column(Integer, nullable=False)
    enhance: Mapped[int] = mapped_column(Integer, nullable=False)
    main_stat: Mapped[str] = mapped_column(String(16), nullable=False)
    main_value: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[int | None] = mapped_column(Integer)
    external_id: Mapped[str | None] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(512), nullable=False)


class GearSubstatRow(Base):
    __tablename__ = "gear_substat"

    gear_id: Mapped[int] = mapped_column(ForeignKey("gear.id", ondelete="CASCADE"), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    stat: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    rolls: Mapped[int | None] = mapped_column(Integer)
    modified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reforged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SnapshotGearRow(Base):
    __tablename__ = "snapshot_gear"

    snapshot_id: Mapped[int] = mapped_column(ForeignKey("hero_snapshot.id", ondelete="CASCADE"), primary_key=True)
    slot: Mapped[str] = mapped_column(String(16), primary_key=True)
    gear_id: Mapped[int] = mapped_column(ForeignKey("gear.id"), nullable=False)
