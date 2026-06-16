"""Persistence service — writes a confirmed portfolio to the DB.

Called from the ``save_to_db`` graph node. Because the graph may resume in a
*different* HTTP request than the one that started it (durable human-in-the-
loop), this opens its OWN session via ``core.db.SessionLocal`` rather than
relying on a request-scoped session.

All money is ``Decimal`` end to end.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from agents.state import AssetItem
from core.db import SessionLocal
from models.asset import Asset
from models.snapshot import Snapshot


def _to_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _sum_usd(assets: list[AssetItem]) -> Decimal | None:
    total = Decimal(0)
    seen = False
    for a in assets:
        if a.usd_value is not None:
            total += a.usd_value
            seen = True
    return total if seen else None


async def save_portfolio(
    *,
    user_id,
    assets: list[AssetItem],
    base_currency: str = "USD",
    raw_input: str | None = None,
    input_type: str | None = "text",
    total_usd: Decimal | None = None,
) -> str:
    """Persist a Snapshot + its Assets. Returns the snapshot id (str)."""
    uid = _to_uuid(user_id)
    if total_usd is None:
        total_usd = _sum_usd(assets)

    async with SessionLocal() as session:
        snapshot = Snapshot(
            id=uuid.uuid4(),
            user_id=uid,
            total_usd=total_usd,
            base_currency=base_currency,
            raw_input=raw_input,
            input_type=input_type,
            is_confirmed=True,
        )
        session.add(snapshot)

        for a in assets:
            session.add(
                Asset(
                    id=uuid.uuid4(),
                    snapshot_id=snapshot.id,
                    user_id=uid,
                    asset_type=a.asset_type,
                    country=a.country,
                    currency=a.currency,
                    amount=a.amount,
                    location=a.location,
                    note=a.note,
                    usd_value=a.usd_value,
                    usd_rate=a.usd_rate,
                    ticker=a.ticker,
                    quantity=a.quantity,
                    symbol=a.symbol,
                    wallet_address=a.wallet_address,
                    interest_rate=a.interest_rate,
                    appreciation_rate=a.appreciation_rate,
                    counterparty=a.counterparty,
                    is_owed_to_me=a.is_owed_to_me,
                )
            )

        await session.commit()
        return str(snapshot.id)
