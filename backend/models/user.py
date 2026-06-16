"""User account model."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    base_currency: Mapped[str] = mapped_column(String(3), default="USD")
    subscription: Mapped[str] = mapped_column(String(20), default="free")
    sub_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Stripe customer id — set at first checkout; used for Billing Portal and to
    # map subscription webhooks back to a user. Null until the user pays.
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Two-factor auth (TOTP). Secret is Fernet-encrypted at rest; recovery codes
    # live (hashed) in `settings["totp_recovery"]`.
    totp_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Bumped to invalidate all previously-issued tokens ("log out everywhere").
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    snapshots: Mapped[list["Snapshot"]] = relationship(  # noqa: F821
        back_populates="user", cascade="all, delete-orphan"
    )
