"""Tracked crypto wallet (read-only, public address) — Feature #6."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base
from models.types import EncryptedString


class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    chain: Mapped[str] = mapped_column(String(10))  # BTC | ETH | TON
    # Public address only — encrypted at rest (it's still personal data).
    address: Mapped[str] = mapped_column(EncryptedString)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
