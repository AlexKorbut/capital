"""Persistence service — writes a confirmed portfolio to the DB.

Called from the ``save_to_db`` graph node. Because the graph may resume in a
*different* HTTP request than the one that started it (durable human-in-the-
loop), this opens its OWN session via ``core.db.SessionLocal`` rather than
relying on a request-scoped session.

All money is ``Decimal`` end to end.

A user has at most one *canonical* snapshot per calendar day in their
timezone (``users.settings["timezone"]``, default Europe/Minsk): the partial
unique index ``uq_snapshots_user_date`` covers rows with ``superseded_at``
NULL. Older same-day rows kept by the migration stay in the table, marked
``superseded_at``, with their assets and foreign keys untouched. Saving again
for that day without ``overwrite`` raises ``SnapshotDateConflict`` (HTTP 409).
With ``overwrite`` the canonical row is updated in one transaction and the id
stays, so advice sessions and scenarios that point at it are not dropped.
``asset_replace="all"`` swaps every asset. ``asset_replace="wallets"`` deletes
only rows that carry a ``wallet_address`` and leaves cash, deposits, property
and any other non-wallet asset in place. The snapshot ``note`` is kept.
``updated_at`` moves on every overwrite; ``created_at`` does not.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agents.state import AssetItem
from core.calendar import local_today
from core.db import SessionLocal
from models.asset import Asset
from models.snapshot import Snapshot
from models.user import User


def _to_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


class SnapshotDateConflict(Exception):
    """A confirmed snapshot already exists for this user and calendar day."""

    def __init__(self, snapshot_date: date, snapshot_id: uuid.UUID) -> None:
        self.snapshot_date = snapshot_date
        self.snapshot_id = snapshot_id
        formatted = snapshot_date.strftime("%d.%m.%Y")
        self.message = f"Снимок за {formatted} уже есть."
        super().__init__(self.message)


def _coerce_date(value) -> date:
    """SQLite sometimes hands a Date column back as a string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def conflict_detail(snapshot_date, snapshot_id: uuid.UUID) -> dict[str, str]:
    """Body of the 409 the API returns when ``overwrite`` was not requested."""
    day = _coerce_date(snapshot_date)
    formatted = day.strftime("%d.%m.%Y")
    return {
        "code": "snapshot_exists",
        "message": f"Снимок за {formatted} уже есть.",
        "snapshot_date": day.isoformat(),
        "snapshot_id": str(snapshot_id),
    }


def _sum_usd(assets: list[AssetItem]) -> Decimal | None:
    total = Decimal(0)
    seen = False
    for a in assets:
        if a.usd_value is not None:
            total += a.usd_value
            seen = True
    return total if seen else None


def _asset_row(snapshot_id: uuid.UUID, user_id: uuid.UUID, item: AssetItem) -> Asset:
    return Asset(
        id=uuid.uuid4(),
        snapshot_id=snapshot_id,
        user_id=user_id,
        asset_type=item.asset_type,
        country=item.country,
        currency=item.currency,
        amount=item.amount,
        location=item.location,
        note=item.note,
        usd_value=item.usd_value,
        usd_rate=item.usd_rate,
        ticker=item.ticker,
        quantity=item.quantity,
        symbol=item.symbol,
        wallet_address=item.wallet_address,
        interest_rate=item.interest_rate,
        appreciation_rate=item.appreciation_rate,
        counterparty=item.counterparty,
        is_owed_to_me=item.is_owed_to_me,
    )


def _is_wallet_row(row: Asset) -> bool:
    return bool((row.wallet_address or "").strip())


def _sum_rows(rows: list[Asset]) -> Decimal | None:
    total = Decimal(0)
    seen = False
    for row in rows:
        if row.usd_value is not None:
            total += row.usd_value
            seen = True
    return total if seen else None


async def _settings(session: AsyncSession, user_id) -> dict:
    user = await session.get(User, _to_uuid(user_id))
    if user is None or not isinstance(user.settings, dict):
        return {}
    return user.settings


async def find_snapshot(
    db: AsyncSession, user_id, day: date | None = None
) -> Snapshot | None:
    """The user's canonical snapshot for ``day``.

    ``day`` defaults to today in the user's timezone (Europe/Minsk when unset).
    Rows marked ``superseded_at`` are history from before the one-per-day rule
    and do not occupy the slot.
    """
    if day is None:
        day = local_today(await _settings(db, user_id))
    return await db.scalar(
        select(Snapshot).where(
            Snapshot.user_id == _to_uuid(user_id),
            Snapshot.snapshot_date == day,
            Snapshot.superseded_at.is_(None),
        )
    )


async def _lock_existing(session: AsyncSession, user_id: uuid.UUID, day: date) -> Snapshot | None:
    stmt = select(Snapshot).where(
        Snapshot.user_id == user_id,
        Snapshot.snapshot_date == day,
        Snapshot.superseded_at.is_(None),
    )
    bind = session.bind
    if bind is not None and bind.dialect.name != "sqlite":
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def save_portfolio(
    *,
    user_id,
    assets: list[AssetItem],
    base_currency: str = "USD",
    raw_input: str | None = None,
    input_type: str | None = "text",
    total_usd: Decimal | None = None,
    snapshot_date: date | None = None,
    created_at: datetime | None = None,
    overwrite: bool = False,
    asset_replace: str = "all",
    now: datetime | None = None,
) -> str:
    """Persist a Snapshot + its Assets. Returns the snapshot id (str).

    ``snapshot_date`` defaults to today in the user's timezone. When that day
    already has a snapshot and ``overwrite`` is false, raises
    ``SnapshotDateConflict`` and writes nothing. When ``overwrite`` is true the
    existing row is updated in the same transaction. ``id``, ``created_at``,
    ``snapshot_date`` and ``note`` stay. ``asset_replace="all"`` replaces every
    asset and rewrites raw input / input type. ``asset_replace="wallets"``
    replaces only assets that have a wallet address and recomputes the total
    from what remains plus the new balances; raw input and input type stay.
    ``updated_at`` is set to ``now``. Advice sessions and scenarios keep
    pointing at that id. Charts and returns are derived on read from these
    rows, so they follow the new total without a separate cache.
    """
    uid = _to_uuid(user_id)
    moment = _aware(now or datetime.now(timezone.utc))
    wallets_only = asset_replace == "wallets"
    if total_usd is None and not wallets_only:
        total_usd = _sum_usd(assets)

    integrity_error: IntegrityError | None = None
    async with SessionLocal() as session:
        try:
            day = snapshot_date or local_today(await _settings(session, uid), moment)
            target = await _lock_existing(session, uid, day)
            if target is not None and not overwrite:
                raise SnapshotDateConflict(day, target.id)

            if target is None:
                created = _aware(created_at or moment)
                target = Snapshot(
                    id=uuid.uuid4(),
                    user_id=uid,
                    created_at=created,
                    updated_at=created,
                    snapshot_date=day,
                    total_usd=total_usd if not wallets_only else _sum_usd(assets),
                    base_currency=base_currency,
                    raw_input=raw_input,
                    input_type=input_type,
                    is_confirmed=True,
                )
                session.add(target)
                for item in assets:
                    session.add(_asset_row(target.id, uid, item))
            elif wallets_only:
                old_assets = list(
                    await session.scalars(select(Asset).where(Asset.snapshot_id == target.id))
                )
                for row in old_assets:
                    if _is_wallet_row(row):
                        await session.delete(row)
                for item in assets:
                    session.add(_asset_row(target.id, uid, item))
                await session.flush()
                kept = list(
                    await session.scalars(select(Asset).where(Asset.snapshot_id == target.id))
                )
                target.total_usd = _sum_rows(kept)
                target.updated_at = moment
                target.is_confirmed = True
            else:
                old_assets = list(
                    await session.scalars(select(Asset).where(Asset.snapshot_id == target.id))
                )
                for row in old_assets:
                    await session.delete(row)
                target.total_usd = total_usd
                target.base_currency = base_currency
                target.raw_input = raw_input
                target.input_type = input_type
                target.updated_at = moment
                target.is_confirmed = True
                for item in assets:
                    session.add(_asset_row(target.id, uid, item))

            await session.flush()
            saved_id = str(target.id)
            await session.commit()
            return saved_id
        except SnapshotDateConflict:
            await session.rollback()
            raise
        except IntegrityError as exc:
            await session.rollback()
            integrity_error = exc
        except Exception:
            await session.rollback()
            raise

    assert integrity_error is not None
    async with SessionLocal() as session:
        existing = await find_snapshot(session, uid, day)
    if existing is not None:
        raise SnapshotDateConflict(day, existing.id) from integrity_error
    raise integrity_error
