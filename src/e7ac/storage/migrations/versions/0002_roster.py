"""Roster: owned heroes, immutable build snapshots, gear and substats (M3).

Revision ID: 0002_roster
Revises: 0001_catalog
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from e7ac.storage.models import UTCDateTime

revision: str = "0002_roster"
down_revision: str | None = "0001_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "owned_hero",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", sa.String(length=32), nullable=False),
        sa.Column("hero_code", sa.String(length=16), nullable=False),
        sa.Column("arena_relevant", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uid"),
    )
    op.create_index("ix_owned_hero_hero_code", "owned_hero", ["hero_code"])
    op.create_table(
        "hero_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", sa.String(length=32), nullable=False),
        sa.Column("owned_hero_id", sa.Integer(), nullable=False),
        sa.Column("captured_at", UTCDateTime(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("awakening", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("skill_s1", sa.Integer(), nullable=False),
        sa.Column("skill_s2", sa.Integer(), nullable=False),
        sa.Column("skill_s3", sa.Integer(), nullable=False),
        sa.Column("imprint_grade", sa.String(length=4), nullable=True),
        sa.Column("imprint_stat", sa.String(length=16), nullable=True),
        sa.Column("imprint_value", sa.Float(), nullable=True),
        sa.Column("ee_stat", sa.String(length=16), nullable=True),
        sa.Column("ee_value", sa.Float(), nullable=True),
        sa.Column("ee_option_code", sa.String(length=32), nullable=True),
        sa.Column("ee_option_text", sa.Text(), nullable=True),
        sa.Column("artifact_code", sa.String(length=16), nullable=True),
        sa.Column("artifact_level", sa.Integer(), nullable=True),
        sa.Column("atk", sa.Integer(), nullable=True),
        sa.Column("defense", sa.Integer(), nullable=True),
        sa.Column("hp", sa.Integer(), nullable=True),
        sa.Column("speed", sa.Integer(), nullable=True),
        sa.Column("crit_chance", sa.Float(), nullable=True),
        sa.Column("crit_damage", sa.Float(), nullable=True),
        sa.Column("effectiveness", sa.Float(), nullable=True),
        sa.Column("effect_resistance", sa.Float(), nullable=True),
        sa.Column("dual_attack", sa.Float(), nullable=True),
        sa.Column("cp", sa.Integer(), nullable=True),
        sa.Column("confidence_json", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["owned_hero_id"], ["owned_hero.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uid"),
    )
    op.create_index("ix_hero_snapshot_owned_hero_id", "hero_snapshot", ["owned_hero_id"])
    op.create_table(
        "gear",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slot", sa.String(length=16), nullable=False),
        sa.Column("set_code", sa.String(length=32), nullable=False),
        sa.Column("grade", sa.String(length=16), nullable=False),
        sa.Column("item_level", sa.Integer(), nullable=False),
        sa.Column("enhance", sa.Integer(), nullable=False),
        sa.Column("main_stat", sa.String(length=16), nullable=False),
        sa.Column("main_value", sa.Float(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("external_id", sa.String(length=64), nullable=True),
        sa.Column("fingerprint", sa.String(length=512), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gear_fingerprint", "gear", ["fingerprint"])
    op.create_index("ix_gear_external_id", "gear", ["external_id"])
    op.create_table(
        "gear_substat",
        sa.Column("gear_id", sa.Integer(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("stat", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("rolls", sa.Integer(), nullable=True),
        sa.Column("modified", sa.Boolean(), nullable=False),
        sa.Column("reforged", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["gear_id"], ["gear.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("gear_id", "idx"),
    )
    op.create_table(
        "snapshot_gear",
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("slot", sa.String(length=16), nullable=False),
        sa.Column("gear_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["gear_id"], ["gear.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["hero_snapshot.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("snapshot_id", "slot"),
    )


def downgrade() -> None:
    op.drop_table("snapshot_gear")
    op.drop_table("gear_substat")
    op.drop_index("ix_gear_external_id", table_name="gear")
    op.drop_index("ix_gear_fingerprint", table_name="gear")
    op.drop_table("gear")
    op.drop_index("ix_hero_snapshot_owned_hero_id", table_name="hero_snapshot")
    op.drop_table("hero_snapshot")
    op.drop_index("ix_owned_hero_hero_code", table_name="owned_hero")
    op.drop_table("owned_hero")
