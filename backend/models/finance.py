"""Finance projects — spend tracked per project in any currency (fin-01).

A *project* (e.g. "Машина") collects *expenses*, each in its own currency. Every
expense also stores its value in the project's base currency, converted at the
rate of the expense date (``base_amount`` + the rate that produced it), so a
report never depends on today's rate. ``fx_rates_daily`` is the shared, global
cache of those dated rates (USD pivot, no user data).

An expense may carry up to a few *attachments* (fin-02): photos of the receipt. The row
holds only metadata; the bytes live in the storage backend under ``storage_key``.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base
from models.types import EncryptedString


class FinProject(Base):
    __tablename__ = "fin_projects"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_fin_project_user_name"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_fin_project_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    base_currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(10), default="active", server_default="active")
    started_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FinExpense(Base):
    __tablename__ = "fin_expenses"
    __table_args__ = (
        UniqueConstraint("user_id", "client_id", name="uq_fin_expense_user_client"),
        CheckConstraint("amount <> 0", name="ck_fin_expense_amount_nonzero"),
        Index("ix_fin_expenses_project_spent", "project_id", "spent_on"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fin_projects.id", ondelete="CASCADE")
    )
    # Negative = refund; zero is never stored.
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    currency: Mapped[str] = mapped_column(String(3))
    spent_on: Mapped[date] = mapped_column(Date)
    category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    note: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    # Value in the project's base currency; NULL = "no rate" (can be re-priced later).
    base_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    # Base-currency units per 1 unit of ``currency`` (what produced base_amount).
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    # The date the rate is actually valid for (a weekend expense gets Friday's).
    fx_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # ecb | nbrb | nbg | cbr | manual | latest
    fx_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Client-supplied idempotency key: a retried POST returns the same expense.
    client_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FinAttachment(Base):
    """Metadata of one receipt photo; the file itself is in ``core.storage``."""

    __tablename__ = "fin_attachments"
    __table_args__ = (UniqueConstraint("expense_id", "sha256", name="uq_fin_attachment_expense_sha"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    expense_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fin_expenses.id", ondelete="CASCADE")
    )
    # Built by the server only: u/{user_id}/{expense_id}/{id}.{ext}. Never shown to clients.
    storage_key: Mapped[str] = mapped_column(String(255))
    # Decided from the file's own bytes (image/jpeg | image/png | image/webp), not from the client.
    mime: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(Integer)
    # Of the plain bytes: the same picture twice on one expense is one attachment.
    sha256: Mapped[str] = mapped_column(String(64))
    # What the client called the file (cut to 200 characters before it is stored). For display
    # only; it never reaches a path or a header. A file name can say a lot, so it is encrypted
    # at rest like the notes are.
    original_name: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FxRateDaily(Base):
    """Global cache of dated FX rates (USD per 1 unit of ``currency``). No user data."""

    __tablename__ = "fx_rates_daily"
    __table_args__ = (UniqueConstraint("date", "currency", name="uq_fx_daily_date_currency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Column is called ``date`` (spec); the attribute avoids shadowing the type.
    rate_date: Mapped[date] = mapped_column("date", Date)
    currency: Mapped[str] = mapped_column(String(3))
    usd_per_unit: Mapped[Decimal] = mapped_column(Numeric(20, 10))
    source: Mapped[str] = mapped_column(String(20))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
