"""Catalog snapshots, facts and resolved entities (M2).

Revision ID: 0001_catalog
Revises:
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from e7ac.storage.models import UTCDateTime

revision: str = "0001_catalog"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("world", sa.String(length=32), nullable=False),
        sa.Column("sources_json", sa.Text(), nullable=False),
        sa.Column("entity_count", sa.Integer(), nullable=False),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_sha256"),
    )
    op.create_table(
        "catalog_fact",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("field", sa.String(length=64), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["catalog_snapshot.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_catalog_fact_entity", "catalog_fact", ["snapshot_id", "entity_type", "entity_id"])
    op.create_table(
        "catalog_entity",
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["catalog_snapshot.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("snapshot_id", "entity_type", "entity_id"),
    )


def downgrade() -> None:
    op.drop_table("catalog_entity")
    op.drop_index("ix_catalog_fact_entity", table_name="catalog_fact")
    op.drop_table("catalog_fact")
    op.drop_table("catalog_snapshot")
