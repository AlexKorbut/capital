"""Portfolio snapshot — state of a portfolio at a point in time.

One *canonical* snapshot per user per calendar day in the user's timezone
(``users.settings["timezone"]``, default Europe/Minsk): ``superseded_at`` is
NULL. A later save for that day replaces that row in place (see
``services.persistence.save_portfolio``). Older same-day rows left behind by
the migration stay in the table with ``superseded_at`` set — they are not
deleted. The partial unique index ignores them.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base
from models.types import EncryptedString
from core.calendar import local_today


def _default_snapshot_day() -> date:
    return local_today(None)


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (
        # One live snapshot per day. Rows the migration marked superseded may
        # share that date; they are history, not a second current snapshot.
        Index(
            "uq_snapshots_user_date",
            "user_id",
            "snapshot_date",
            unique=True,
            sqlite_where=text("superseded_at IS NULL"),
            postgresql_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # Bumped when the row is overwritten (full replace or wallet sync).
    # Reminders and the dashboard timestamp follow this, not ``created_at``.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=func.now(),
    )
    # Calendar day this snapshot represents, in the user's timezone.
    snapshot_date: Mapped[date] = mapped_column(Date, default=_default_snapshot_day)
    # Set on an older same-day row when a later snapshot of that day is the
    # canonical one. NULL means this row occupies the day's unique slot.
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
