"""add finance attachments: fin_attachments (receipt photos on expenses)

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fin_attachments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "expense_id",
            sa.Uuid(),
            sa.ForeignKey("fin_expenses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("mime", sa.String(length=40), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("original_name", sa.Text(), nullable=True),  # EncryptedString -> Text
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("expense_id", "sha256", name="uq_fin_attachment_expense_sha"),
    )
    op.create_index("ix_fin_attachments_user_id", "fin_attachments", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_fin_attachments_user_id", table_name="fin_attachments")
    op.drop_table("fin_attachments")
