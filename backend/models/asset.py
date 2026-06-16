"""Asset model — one financial asset belonging to a snapshot."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base
from models.types import EncryptedString


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # Core fields
    asset_type: Mapped[str] = mapped_column(String(30), index=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)

    # Market enrichment
    usd_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    usd_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)

    # Stocks
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    broker: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Crypto
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    wallet_address: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)

    # Real estate / vehicles — annual appreciation(+)/depreciation(−) rate, used
    # to estimate the current value between snapshots.
    appreciation_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    # Bank deposit
    interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    maturity_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Debt
    counterparty: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    is_owed_to_me: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    snapshot: Mapped["Snapshot"] = relationship(back_populates="assets")  # noqa: F821
