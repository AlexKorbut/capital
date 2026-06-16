"""Enrichment service (Agent #7) — attach a USD value to every asset.

Pure Decimal math on top of ``services.market``. Designed to degrade
gracefully: if a rate/price is unavailable (offline, unsupported currency),
the asset keeps ``usd_value = None`` instead of raising — the portfolio still
saves and the missing value is simply excluded from the total.

Sign convention (so ``sum(usd_value)`` == net worth):
  - Debts you OWE (``is_owed_to_me is False``) contribute a NEGATIVE usd_value.
  - Everything else (including money owed TO you) is positive.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from agents.state import AssetItem
from services import market

logger = logging.getLogger("kapital.enrichment")

_USD_QUANT = Decimal("0.01")


def _quantize_usd(value: Decimal) -> Decimal:
    return value.quantize(_USD_QUANT)


async def _rate_and_value(asset: AssetItem) -> tuple[Decimal | None, Decimal | None]:
    """Return (usd_rate_per_unit, usd_value) for one asset, or (None, None)."""
    amount = asset.amount
    if amount is None:
        return None, None

    if asset.asset_type == "crypto":
        symbol = (asset.symbol or asset.currency or "").upper()
        price = await market.usd_price_for_crypto(symbol)
        if price is None:
            return None, None
        qty = asset.quantity if asset.quantity is not None else amount
        return price, _quantize_usd(qty * price)

    if asset.asset_type == "stock":
        ticker = (asset.ticker or asset.symbol or asset.currency or "").upper()
        price = await market.usd_price_for_stock(ticker)
        if price is None:
            return None, None
        # `amount` = number of shares; `quantity` mirrors it when set.
        shares = asset.quantity if asset.quantity is not None else amount
        return price, _quantize_usd(shares * price)

    # Fiat-denominated assets (cash, bank_deposit, real_estate, debt, other).
    rate = await market.usd_rate_for_currency(asset.currency or "USD")
    if rate is None:
        return None, None
    value = _quantize_usd(amount * rate)

    if asset.asset_type == "debt" and asset.is_owed_to_me is False:
        value = -value

    return rate, value


async def enrich_one(asset: AssetItem) -> tuple[Decimal | None, Decimal | None]:
    """Compute (usd_rate, usd_value) for a single asset. Never raises."""
    try:
        return await _rate_and_value(asset)
    except Exception as e:  # noqa: BLE001
        logger.warning("enrich_one failed for %s: %s", asset.asset_type, e)
        return None, None


async def enrich_assets(
    assets: list[AssetItem], base_currency: str = "USD"
) -> tuple[list[AssetItem], Decimal | None]:
    """Fill usd_rate/usd_value on each asset; return (assets, total_usd).

    ``total_usd`` is None only when NO asset could be priced.
    """
    total = Decimal(0)
    priced_any = False

    for asset in assets:
        try:
            rate, value = await _rate_and_value(asset)
        except Exception as e:  # noqa: BLE001 — never let enrichment crash a save
            logger.warning("enrich failed for %s: %s", asset.asset_type, e)
            rate, value = None, None

        asset.usd_rate = rate
        asset.usd_value = value
        if value is not None:
            total += value
            priced_any = True

    return assets, (_quantize_usd(total) if priced_any else None)
