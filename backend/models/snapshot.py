"""Portfolio snapshot — state of a portfolio at a point in time."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base
from models.types import EncryptedString


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    total_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    total_base: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    base_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # raw_input may contain sensitive financial detail -> encrypted at rest.
    raw_input: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    input_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="snapshots")  # noqa: F821
    assets: Mapped[list["Asset"]] = relationship(  # noqa: F821
        back_populates="snapshot", cascade="all, delete-orphan"
    )
