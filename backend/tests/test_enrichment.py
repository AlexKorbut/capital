"""Срез 2 verification — enrichment Decimal math + dashboard read side."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from agents.state import AssetItem

# Fixed rates for deterministic math (1 unit -> USD).
_FX = {"EUR": Decimal("1.08"), "GEL": Decimal("0.37"), "USD": Decimal("1")}
_CRYPTO = {"BTC": Decimal("60000")}


@pytest.fixture(autouse=True)
def _fixed_market(monkeypatch):
    import services.market as market

    async def fx(ccy: str):
        return _FX.get((ccy or "").upper())

    async def crypto(symbol: str):
        return _CRYPTO.get((symbol or "").upper())

    monkeypatch.setattr(market, "usd_rate_for_currency", fx)
    monkeypatch.setattr(market, "usd_price_for_crypto", crypto)


async def test_enrich_reference_portfolio_decimal():
    from services.enrichment import enrich_assets

    assets = [
        AssetItem(asset_type="cash", amount=Decimal("10000"), currency="EUR"),
        AssetItem(asset_type="crypto", amount=Decimal("2"), currency="BTC", symbol="BTC"),
        AssetItem(
            asset_type="bank_deposit", amount=Decimal("1000"), currency="GEL",
            interest_rate=Decimal("9"),
        ),
    ]
    enriched, total = await enrich_assets(assets)

    cash, btc, dep = enriched
    assert cash.usd_value == Decimal("10800.00")      # 10000 * 1.08
    assert cash.usd_rate == Decimal("1.08")
    assert btc.usd_value == Decimal("120000.00")      # 2 * 60000
    assert dep.usd_value == Decimal("370.00")         # 1000 * 0.37
    # total = 10800 + 120000 + 370
    assert total == Decimal("131170.00")
    assert all(isinstance(a.usd_value, Decimal) for a in enriched)


async def test_debt_owed_by_user_subtracts():
    from services.enrichment import enrich_assets

    assets = [
        AssetItem(asset_type="cash", amount=Decimal("5000"), currency="USD"),
        AssetItem(
            asset_type="debt", amount=Decimal("2000"), currency="USD",
            is_owed_to_me=False,  # the user OWES this
        ),
        AssetItem(
            asset_type="debt", amount=Decimal("1000"), currency="USD",
            is_owed_to_me=True,   # owed TO the user
        ),
    ]
    enriched, total = await enrich_assets(assets)
    assert enriched[1].usd_value == Decimal("-2000.00")
    assert enriched[2].usd_value == Decimal("1000.00")
    assert total == Decimal("4000.00")  # 5000 - 2000 + 1000


async def test_unpriced_asset_keeps_none_and_total_excludes():
    from services.enrichment import enrich_assets

    assets = [
        AssetItem(asset_type="cash", amount=Decimal("100"), currency="USD"),
        AssetItem(asset_type="stock", amount=Decimal("10"), ticker="AAPL"),  # no price source
        AssetItem(asset_type="cash", amount=Decimal("50"), currency="XYZ"),  # unknown ccy
    ]
    enriched, total = await enrich_assets(assets)
    assert enriched[0].usd_value == Decimal("100.00")
    assert enriched[1].usd_value is None
    assert enriched[2].usd_value is None
    assert total == Decimal("100.00")


async def test_current_portfolio_breakdown(db_ready):
    """End-to-end read: persist an enriched snapshot, query the dashboard view."""
    from core.db import SessionLocal
    from models.asset import Asset
    from models.snapshot import Snapshot
    from models.user import User
    from services import portfolio_read

    uid = uuid.uuid4()
    async with SessionLocal() as s:
        s.add(User(id=uid, email=f"{uid}@t.test", password_hash="x", base_currency="USD"))
        snap = Snapshot(
            id=uuid.uuid4(), user_id=uid, total_usd=Decimal("131170.00"),
            base_currency="USD", is_confirmed=True,
        )
        s.add(snap)
        s.add(Asset(
            id=uuid.uuid4(), snapshot_id=snap.id, user_id=uid, asset_type="cash",
            currency="EUR", country="BY", amount=Decimal("10000"),
            usd_value=Decimal("10800.00"),
        ))
        s.add(Asset(
            id=uuid.uuid4(), snapshot_id=snap.id, user_id=uid, asset_type="crypto",
            currency="BTC", symbol="BTC", amount=Decimal("2"),
            usd_value=Decimal("120000.00"),
        ))
        await s.commit()

    async with SessionLocal() as s:
        data = await portfolio_read.current_portfolio(s, uid)

    assert data is not None
    assert data["total_usd"] == "131170.00"
    assert len(data["assets"]) == 2
    btype = {e["key"]: e["usd_value"] for e in data["breakdown"]["by_type"]}
    assert btype["crypto"] == "120000.00"
    assert btype["cash"] == "10800.00"
    bcur = {e["key"]: e["usd_value"] for e in data["breakdown"]["by_currency"]}
    assert bcur["BTC"] == "120000.00"
