"""Read-side portfolio queries for the dashboard.

Returns plain dicts of JSON-serialisable values (Decimals -> str) so routers can
hand them straight to Pydantic response models.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.asset import Asset
from models.snapshot import Snapshot

# Asset types whose value drifts with a stored annual appreciation rate.
_APPRECIATING = {"real_estate", "vehicle"}


def _d(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _estimated_usd(a: Asset, as_of: datetime | None) -> Decimal | None:
    """Project a real-estate/vehicle value forward by its appreciation rate.

    value_now = usd_value * (1 + rate/100) ** years_since_snapshot. Returns the
    plain usd_value for everything else (stocks/crypto already update at save).
    """
    if a.usd_value is None:
        return None
    if a.asset_type not in _APPRECIATING or not a.appreciation_rate or as_of is None:
        return a.usd_value
    now = datetime.now(timezone.utc)
    base = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    years = max((now - base).days, 0) / 365.0
    if years == 0:
        return a.usd_value
    factor = (1.0 + float(a.appreciation_rate) / 100.0) ** years
    return (a.usd_value * Decimal(str(factor))).quantize(Decimal("0.01"))


def _to_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def latest_snapshot(db: AsyncSession, user_id) -> Snapshot | None:
    return await db.scalar(
        select(Snapshot)
        .where(Snapshot.user_id == _to_uuid(user_id), Snapshot.is_confirmed.is_(True))
        .order_by(Snapshot.created_at.desc())
        .limit(1)
    )


def _asset_dict(a: Asset, as_of: datetime | None = None) -> dict:
    est = _estimated_usd(a, as_of)
    return {
        "id": str(a.id),
        "asset_type": a.asset_type,
        "amount": _d(a.amount),
        "currency": a.currency,
        "country": a.country,
        "location": a.location,
        "usd_value": _d(a.usd_value),
        "usd_rate": _d(a.usd_rate),
        "symbol": a.symbol,
        "ticker": a.ticker,
        "quantity": _d(a.quantity),
        "interest_rate": _d(a.interest_rate),
        "appreciation_rate": _d(a.appreciation_rate),
        "estimated_usd": _d(est),
        "is_owed_to_me": a.is_owed_to_me,
    }


def _breakdown(assets: list[Asset]) -> dict[str, list[dict]]:
    """Aggregate usd_value by type / currency / country."""
    by_type: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    by_currency: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    by_country: dict[str, Decimal] = defaultdict(lambda: Decimal(0))

    for a in assets:
        if a.usd_value is None:
            continue
        by_type[a.asset_type] += a.usd_value
        by_currency[a.currency or "—"] += a.usd_value
        by_country[a.country or "—"] += a.usd_value

    def fmt(d: dict[str, Decimal]) -> list[dict]:
        return [
            {"key": k, "usd_value": _d(v)}
            for k, v in sorted(d.items(), key=lambda kv: kv[1], reverse=True)
        ]

    return {
        "by_type": fmt(by_type),
        "by_currency": fmt(by_currency),
        "by_country": fmt(by_country),
    }


async def current_portfolio(db: AsyncSession, user_id, base_currency: str = "USD") -> dict | None:
    snap = await latest_snapshot(db, user_id)
    if snap is None:
        return None

    assets = list(
        await db.scalars(
            select(Asset)
            .where(Asset.snapshot_id == snap.id)
            .order_by(Asset.usd_value.desc().nullslast())
        )
    )

    # Estimated "today" total (appreciating assets projected forward).
    est_total = Decimal(0)
    saw = False
    for a in assets:
        e = _estimated_usd(a, snap.created_at)
        if e is not None:
            est_total += e
            saw = True
    estimated_total = est_total if saw else None

    # Base-currency normalisation for display (USD stays canonical).
    base = (base_currency or "USD").upper()
    from services import market

    usd_per_base = await market.usd_per_unit(base)  # USD per 1 base unit

    return {
        "snapshot_id": str(snap.id),
        "created_at": snap.created_at.isoformat() if snap.created_at else None,
        "total_usd": _d(snap.total_usd),
        "estimated_total_usd": _d(estimated_total),
        "base_currency": base,
        "usd_per_base": _d(usd_per_base),
        "assets": [_asset_dict(a, snap.created_at) for a in assets],
        "breakdown": _breakdown(assets),
    }


async def list_snapshots(db: AsyncSession, user_id, limit: int = 50) -> list[dict]:
    rows = list(
        await db.scalars(
            select(Snapshot)
            .where(Snapshot.user_id == _to_uuid(user_id), Snapshot.is_confirmed.is_(True))
            .order_by(Snapshot.created_at.desc())
            .limit(limit)
        )
    )
    return [
        {
            "snapshot_id": str(s.id),
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "total_usd": _d(s.total_usd),
            "base_currency": s.base_currency,
        }
        for s in rows
    ]


async def history_chart(db: AsyncSession, user_id, limit: int = 365) -> list[dict]:
    """Ascending time series of net worth for charting."""
    rows = list(
        await db.scalars(
            select(Snapshot)
            .where(
                Snapshot.user_id == _to_uuid(user_id),
                Snapshot.is_confirmed.is_(True),
                Snapshot.total_usd.is_not(None),
            )
            .order_by(Snapshot.created_at.asc())
            .limit(limit)
        )
    )
    return [
        {
            "date": s.created_at.isoformat() if s.created_at else None,
            "total_usd": _d(s.total_usd),
        }
        for s in rows
    ]
