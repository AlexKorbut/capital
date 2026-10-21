"""one canonical portfolio snapshot per user per calendar day

Historical duplicates are not deleted. The day is the Europe/Minsk calendar
day of ``created_at`` (stored as UTC). The latest row of each (user, day)
stays canonical (``superseded_at`` NULL). Every earlier row of that day keeps
its id, assets, note and foreign keys (advice, scenarios) and is only stamped
``superseded_at`` with the later snapshot's ``created_at``. A partial unique
index then allows those marked rows to share the date. ``updated_at`` is
backfilled from ``created_at``.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-21 12:00:00.000000

"""
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Sequence, Union
from zoneinfo import ZoneInfo

from alembic import op
import sqlalchemy as sa

# Historical rows have no per-user timezone stored. The owner is in Minsk, and
# this revision has not been applied anywhere, so every backfilled day is Minsk.
_BACKFILL_ZONE = ZoneInfo("Europe/Minsk")


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _as_datetime(value) -> datetime:
    """UTC timestamp for ordering. SQLite may store ``created_at`` as text with
    either a space or a ``T`` separator; string sort would pick the wrong row.
    """
    if isinstance(value, datetime):
        base = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return base.astimezone(timezone.utc)
    text = str(value or "").strip().replace(" ", "T", 1)
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_date(value) -> date:
    """Calendar day of a stored ``created_at``, in Europe/Minsk.

    Naive values are UTC (how the app stores them). 00:30 in Minsk is still
    the previous UTC date, and must land on the Minsk day.
    """
    if isinstance(value, datetime):
        return _as_datetime(value).astimezone(_BACKFILL_ZONE).date()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    try:
        return _as_datetime(text).astimezone(_BACKFILL_ZONE).date()
    except ValueError:
        return datetime.now(timezone.utc).astimezone(_BACKFILL_ZONE).date()


def _backfill_snapshot_dates() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, created_at FROM snapshots WHERE snapshot_date IS NULL")
    ).mappings()
    for row in rows:
        conn.execute(
            sa.text("UPDATE snapshots SET snapshot_date = :day WHERE id = :id"),
            {"day": _as_date(row["created_at"]).isoformat(), "id": row["id"]},
        )


def _mark_older_same_day_snapshots() -> None:
    """Stamp every earlier same-day row. Nothing is deleted and no FK moves.

    ``superseded_at`` is the canonical row's ``created_at`` (when that later
    snapshot was saved). Ties break on id, ascending, so the choice is stable.
    """
    conn = op.get_bind()
    rows = list(
        conn.execute(
            sa.text("SELECT id, user_id, snapshot_date, created_at FROM snapshots")
        ).mappings()
    )
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for row in rows:
        groups[(str(row["user_id"]), str(row["snapshot_date"])[:10])].append(row)

    stamped = datetime.now(timezone.utc).isoformat()
    for group in groups.values():
        if len(group) < 2:
            continue
        # Parsed time, then id. String order is wrong when one row is
        # ``2026-10-21 09:00:00`` and another is ``2026-10-21T08:00:00``.
        group.sort(key=lambda r: (_as_datetime(r["created_at"]), str(r["id"])))
        keeper = group[-1]
        marked_at = keeper["created_at"] or stamped
        for older in group[:-1]:
            conn.execute(
                sa.text("UPDATE snapshots SET superseded_at = :marked WHERE id = :id"),
                {"marked": marked_at, "id": older["id"]},
            )


def _backfill_updated_at() -> None:
    """Overwrite time starts equal to ``created_at``; nothing has been edited yet."""
    op.get_bind().execute(
        sa.text("UPDATE snapshots SET updated_at = created_at WHERE updated_at IS NULL")
    )


def upgrade() -> None:
    # ADD COLUMN is portable. NOT NULL is not a plain ALTER on SQLite, so that
    # one change goes through batch mode after dates are filled in. The unique
    # index is partial (canonical rows only) and is a plain CREATE INDEX.
    op.add_column("snapshots", sa.Column("snapshot_date", sa.Date(), nullable=True))
    op.add_column("snapshots", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("snapshots", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    _backfill_snapshot_dates()
    _mark_older_same_day_snapshots()
    _backfill_updated_at()
    with op.batch_alter_table("snapshots") as batch:
        batch.alter_column("snapshot_date", existing_type=sa.Date(), nullable=False)
        batch.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        )
    op.create_index(
        "uq_snapshots_user_date",
        "snapshots",
        ["user_id", "snapshot_date"],
        unique=True,
        sqlite_where=sa.text("superseded_at IS NULL"),
        postgresql_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_snapshots_user_date", table_name="snapshots")
    with op.batch_alter_table("snapshots") as batch:
        batch.drop_column("snapshot_date")
        batch.drop_column("superseded_at")
        batch.drop_column("updated_at")
