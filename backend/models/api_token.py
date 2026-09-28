"""Personal API tokens — machine access for hosts such as Jarvis.

A token is shown to the user exactly once at creation; only its SHA-256 hash is
stored. Tokens are independent of the JWT ``token_version`` ("log out
everywhere" keeps API tokens alive — they are revoked explicitly instead).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    # SHA-256 hex of the plaintext token; the plaintext is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # First characters of the plaintext ("kp_ab12cd34") so the user can tell
    # tokens apart in the list without ever seeing the secret again.
    prefix: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
