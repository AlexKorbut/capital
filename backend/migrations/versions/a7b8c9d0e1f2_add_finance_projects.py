"""add finance projects: fin_projects, fin_expenses, fx_rates_daily

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fin_projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),  # EncryptedString -> Text
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="active", nullable=False),
        sa.Column("started_on", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "name", name="uq_fin_project_user_name"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_fin_project_status"),
    )
    op.create_index("ix_fin_projects_user_id", "fin_projects", ["user_id"])

    op.create_table(
        "fin_expenses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("fin_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("spent_on", sa.Date(), nullable=False),
        sa.Column("category", sa.String(length=60), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),  # EncryptedString -> Text
        sa.Column("base_amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("fx_rate", sa.Numeric(20, 10), nullable=True),
        sa.Column("fx_date", sa.Date(), nullable=True),
        sa.Column("fx_source", sa.String(length=20), nullable=True),
        sa.Column("client_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "client_id", name="uq_fin_expense_user_client"),
        sa.CheckConstraint("amount <> 0", name="ck_fin_expense_amount_nonzero"),
    )
    op.create_index(
        "ix_fin_expenses_project_spent", "fin_expenses", ["project_id", "spent_on"]
    )

    op.create_table(
        "fx_rates_daily",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("usd_per_unit", sa.Numeric(20, 10), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("date", "currency", name="uq_fx_daily_date_currency"),
    )


def downgrade() -> None:
    op.drop_table("fx_rates_daily")
    op.drop_index("ix_fin_expenses_project_spent", table_name="fin_expenses")
    op.drop_table("fin_expenses")
    op.drop_index("ix_fin_projects_user_id", table_name="fin_projects")
    op.drop_table("fin_projects")
