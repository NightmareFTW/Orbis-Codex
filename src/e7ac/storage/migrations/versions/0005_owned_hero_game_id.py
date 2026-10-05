"""Roster: the game's id of an owned hero copy, from Fribbels importer data (SPEC D53), to tell copies apart.

Revision ID: 0005_owned_hero_game_id
Revises: 0004_imprint_mode
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_owned_hero_game_id"
down_revision: str | None = "0004_imprint_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # nullable: heroes entered by hand, scanned or imported from a Fribbels export have no known game id
    with op.batch_alter_table("owned_hero") as batch:
        batch.add_column(sa.Column("game_id", sa.String(length=32), nullable=True))
    op.create_index("ux_owned_hero_game_id", "owned_hero", ["game_id"], unique=True)


def downgrade() -> None:
    # in place (no table copy: with foreign keys on, dropping the old table would cascade-delete the snapshots)
    op.drop_index("ux_owned_hero_game_id", table_name="owned_hero")
    with op.batch_alter_table("owned_hero", recreate="never") as batch:
        batch.drop_column("game_id")
