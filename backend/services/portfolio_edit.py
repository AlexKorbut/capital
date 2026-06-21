"""In-place editing of the current portfolio (add / update / delete one asset).

Edits mutate the LATEST confirmed snapshot directly — they do NOT create a new
snapshot (so they don't burn the monthly snapshot quota) and they re-enrich the
touched asset (fixing stale USD values). History snapshots are still produced by
the normal input → confirm flow.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agents.state import AssetItem
from models.asset import Asset
from models.snapshot import Snapshot
from services import enrichment, portfolio_read


def _to_uuid(v) -> uuid.UUID:
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


def _asset_to_item(a: Asset) -> AssetItem:
    return AssetItem(
        asset_type=a.asset_type,
        amount=a.amount if a.amount is not None else Decimal(0),
        currency=a.currency or "USD",
        country=a.country,
        location=a.location,
        note=a.note,
        ticker=a.ticker,
        symbol=a.symbol,
        quantity=a.quantity,
        interest_rate=a.interest_rate,
        appreciation_rate=a.appreciation_rate,
        wallet_address=a.wallet_address,
        counterparty=a.counterparty,
        is_owed_to_me=a.is_owed_to_me,
    )


def _apply_item_to_row(row: Asset, item: AssetItem) -> None:
    row.asset_type = item.asset_type
    row.amount = item.amount
    row.currency = item.currency
    row.country = item.country
    row.location = item.location
    row.note = item.note
    row.ticker = item.ticker
    row.symbol = item.symbol
    row.quantity = item.quantity
    row.interest_rate = item.interest_rate
    row.appreciation_rate = item.appreciation_rate
    row.wallet_address = item.wallet_address
    row.counterparty = item.counterparty
    row.is_owed_to_me = item.is_owed_to_me


async def _recompute_total(db: AsyncSession, snapshot: Snapshot) -> None:
    rows = list(await db.scalars(select(Asset).where(Asset.snapshot_id == snapshot.id)))
    total = Decimal(0)
    seen = False
    for r in rows:
        if r.usd_value is not None:
            total += r.usd_value
            seen = True
    snapshot.total_usd = total if seen else None


async def _ensure_snapshot(db: AsyncSession, user_id, base_currency: str) -> Snapshot:
    snap = await portfolio_read.latest_snapshot(db, user_id)
    if snap is not None:
        return snap
    snap = Snapshot(
        id=uuid.uuid4(), user_id=_to_uuid(user_id), base_currency=base_currency,
        input_type="manual", is_confirmed=True,
    )
    db.add(snap)
    try:
        await db.flush()
    except IntegrityError:
        # Concurrent first-edit lost the race on the one-manual-snapshot unique
        # index — roll back and reuse the snapshot the other request created.
        await db.rollback()
        existing = await portfolio_read.latest_snapshot(db, user_id)
        if existing is not None:
            return existing
        raise
    return snap


async def add_asset(db: AsyncSession, user_id, item: AssetItem, base_currency: str = "USD"):
    snap = await _ensure_snapshot(db, user_id, base_currency)
    rate, value = await enrichment.enrich_one(item)
    row = Asset(
        id=uuid.uuid4(), snapshot_id=snap.id, user_id=_to_uuid(user_id),
        usd_rate=rate, usd_value=value,
    )
    _apply_item_to_row(row, item)
    db.add(row)
    await db.flush()
    await _recompute_total(db, snap)
    await db.commit()


async def update_asset(db: AsyncSession, user_id, asset_id, item: AssetItem) -> bool:
    row = await db.get(Asset, _to_uuid(asset_id))
    if row is None or row.user_id != _to_uuid(user_id):
        return False
    snap = await portfolio_read.latest_snapshot(db, user_id)
    if snap is None or row.snapshot_id != snap.id:
        return False  # only the current snapshot is editable
    _apply_item_to_row(row, item)
    rate, value = await enrichment.enrich_one(_asset_to_item(row))
    row.usd_rate, row.usd_value = rate, value
    await _recompute_total(db, snap)
    await db.commit()
    return True


async def delete_asset(db: AsyncSession, user_id, asset_id) -> bool:
    row = await db.get(Asset, _to_uuid(asset_id))
    if row is None or row.user_id != _to_uuid(user_id):
        return False
    snap = await portfolio_read.latest_snapshot(db, user_id)
    if snap is None or row.snapshot_id != snap.id:
        return False
    await db.delete(row)
    await db.flush()
    await _recompute_total(db, snap)
    await db.commit()
    return True
