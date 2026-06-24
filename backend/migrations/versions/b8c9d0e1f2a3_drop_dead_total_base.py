"""drop dead snapshots.total_base column

total_base was never written by any code path (the app derives base-currency
values from live FX via usd_per_base), so it only ever held NULL. Drop it.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-21 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("snapshots") as batch_op:
        batch_op.drop_column("total_base")


def downgrade() -> None:
    with op.batch_alter_table("snapshots") as batch_op:
        batch_op.add_column(
            sa.Column("total_base", sa.Numeric(precision=20, scale=2), nullable=True)
        )
