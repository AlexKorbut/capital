"""security hardening: totp replay guard + subscription event idempotency

Adds ``users.totp_last_used_step`` (blocks TOTP code replay) and a unique
constraint on ``subscription_events(payment_provider, provider_ref)`` (makes
webhook idempotency race-safe).

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("totp_last_used_step", sa.BigInteger(), nullable=True)
        )
    with op.batch_alter_table("subscription_events") as batch_op:
        batch_op.create_unique_constraint(
            "uq_subscription_events_provider_ref",
            ["payment_provider", "provider_ref"],
        )


def downgrade() -> None:
    with op.batch_alter_table("subscription_events") as batch_op:
        batch_op.drop_constraint(
            "uq_subscription_events_provider_ref", type_="unique"
        )
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("totp_last_used_step")
