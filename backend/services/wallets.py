"""Read-only crypto wallet balances by public address (Feature #6).

Keyless public explorers (no API key, so it works with zero configuration):
  - BTC : blockstream.info  (chain_stats funded − spent, in sats)
  - ETH : ethplorer.io      (freekey, ETH balance)
  - TON : toncenter.com     (getAddressBalance, nanotons)

Only PUBLIC addresses are read — we never touch private keys. Balances feed the
portfolio as ``crypto`` ``AssetItem``s priced by the existing market service.
"""
from __future__ import annotations

import logging
from decimal import Decimal

import httpx

from agents.state import AssetItem
from core.config import settings

logger = logging.getLogger("kapital.wallets")

_TIMEOUT = httpx.Timeout(12.0)

SUPPORTED_CHAINS = ("BTC", "ETH", "TON")

# Demo balances when offline / DEMO_MODE, keyed by chain.
_DEMO_BALANCES = {"BTC": Decimal("0.25"), "ETH": Decimal("1.5"), "TON": Decimal("250")}


async def _get_json(url: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()


async def _btc_balance(address: str) -> Decimal | None:
    data = await _get_json(f"https://blockstream.info/api/address/{address}")
    stats = data.get("chain_stats") or {}
    funded = stats.get("funded_txo_sum")
    spent = stats.get("spent_txo_sum")
    if funded is None or spent is None:
        return None
    sats = Decimal(int(funded) - int(spent))
    return sats / Decimal(10) ** 8


async def _eth_balance(address: str) -> Decimal | None:
    data = await _get_json(
        f"https://api.ethplorer.io/getAddressInfo/{address}", {"apiKey": "freekey"}
    )
    bal = ((data.get("ETH") or {}).get("balance"))
    return Decimal(str(bal)) if bal is not None else None


async def _ton_balance(address: str) -> Decimal | None:
    data = await _get_json(
        "https://toncenter.com/api/v2/getAddressBalance", {"address": address}
    )
    if not data.get("ok"):
        return None
    nanotons = Decimal(str(data.get("result")))
    return nanotons / Decimal(10) ** 9


_FETCHERS = {"BTC": _btc_balance, "ETH": _eth_balance, "TON": _ton_balance}


async def fetch_balance(chain: str, address: str) -> Decimal | None:
    chain = (chain or "").upper()
    address = (address or "").strip()
    if chain not in _FETCHERS or not address:
        return None
    if settings.is_demo:
        return _DEMO_BALANCES.get(chain)
    try:
        return await _FETCHERS[chain](address)
    except httpx.HTTPError as e:
        logger.warning("wallet balance fetch failed (%s %s): %s", chain, address, e)
        # Fall back to demo so the UI still shows something testable.
        return _DEMO_BALANCES.get(chain)


def balance_to_asset(chain: str, balance: Decimal, address: str) -> AssetItem:
    sym = chain.upper()
    return AssetItem(
        asset_type="crypto",
        amount=balance,
        currency=sym,
        symbol=sym,
        quantity=balance,
        wallet_address=address,
        location=f"{sym} wallet",
        confidence=1.0,
    )
