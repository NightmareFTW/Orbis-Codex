"""Roster: at most one current snapshot per owned hero (partial unique index; M3 review STOR-03).

Revision ID: 0003_one_current_snapshot
Revises: 0002_roster
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_one_current_snapshot"
down_revision: str | None = "0002_roster"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # repair databases written before the index existed: keep the newest current row per hero
    op.execute(
        """
        UPDATE hero_snapshot SET is_current = 0
        WHERE is_current = 1 AND id NOT IN (
            SELECT MAX(id) FROM hero_snapshot WHERE is_current = 1 GROUP BY owned_hero_id
        )
        """
    )
    op.create_index(
        "ux_hero_snapshot_current",
        "hero_snapshot",
        ["owned_hero_id"],
        unique=True,
        sqlite_where=sa.text("is_current = 1"),
    )


def downgrade() -> None:
    op.drop_index("ux_hero_snapshot_current", table_name="hero_snapshot")
