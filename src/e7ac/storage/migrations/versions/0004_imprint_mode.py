"""Roster: imprint mode, self or team (MECH-IMP-02; the hero screens show the active one).

Revision ID: 0004_imprint_mode
Revises: 0003_one_current_snapshot
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_imprint_mode"
down_revision: str | None = "0003_one_current_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # nullable: rows written before this revision keep an unknown mode
    with op.batch_alter_table("hero_snapshot") as batch:
        batch.add_column(sa.Column("imprint_mode", sa.String(length=8), nullable=True))


def downgrade() -> None:
    # ALTER TABLE ... DROP COLUMN (SQLite >= 3.35), never a table copy: with foreign keys on, dropping the old table
    # would cascade-delete every snapshot_gear row (M6.1 review)
    with op.batch_alter_table("hero_snapshot", recreate="never") as batch:
        batch.drop_column("imprint_mode")
