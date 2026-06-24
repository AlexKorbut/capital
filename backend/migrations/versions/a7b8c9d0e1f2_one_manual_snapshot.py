"""one current manual snapshot per user (race-safe in-place edits)

Adds a partial unique index on snapshots(user_id) WHERE input_type='manual',
so two concurrent first-edits can't create duplicate "current" snapshots.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-21 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_one_manual_snapshot",
        "snapshots",
        ["user_id"],
        unique=True,
        sqlite_where=sa.text("input_type = 'manual'"),
        postgresql_where=sa.text("input_type = 'manual'"),
    )


def downgrade() -> None:
    op.drop_index("uq_one_manual_snapshot", table_name="snapshots")
