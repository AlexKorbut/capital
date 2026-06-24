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
import re
from decimal import Decimal
from typing import Optional

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


# BTC addresses: legacy/P2SH base58 (1.../3...) or bech32 (bc1...). Conservative
# charset check — alphanumeric only, no '/', no whitespace — so the address can
# never break out of the explorer URL path.
_BTC_ADDRESS_RE = re.compile(r"^(?:[13][a-km-zA-HJ-NP-Z1-9]{25,39}|bc1[a-z0-9]{11,87})$")
# ETH: 0x + 40 hex. TON: base64url-ish, 48 chars typical (allow '-'/'_').
_ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_TON_ADDRESS_RE = re.compile(r"^[A-Za-z0-9_-]{48,68}$")


async def _btc_balance(address: str) -> Decimal | None:
    if not _BTC_ADDRESS_RE.match(address):
        logger.warning("rejecting malformed BTC address")
        return None
    data = await _get_json(f"https://blockstream.info/api/address/{address}")
    stats = data.get("chain_stats") or {}
    funded = stats.get("funded_txo_sum")
    spent = stats.get("spent_txo_sum")
    if funded is None or spent is None:
        return None
    sats = Decimal(int(funded) - int(spent))
    return sats / Decimal(10) ** 8


async def _eth_balance(address: str) -> Decimal | None:
    # ETH address is also interpolated into the URL path — validate the format.
    if not _ETH_ADDRESS_RE.match(address):
        logger.warning("rejecting malformed ETH address")
        return None
    data = await _get_json(
        f"https://api.ethplorer.io/getAddressInfo/{address}", {"apiKey": "freekey"}
    )
    bal = ((data.get("ETH") or {}).get("balance"))
    return Decimal(str(bal)) if bal is not None else None


async def _ton_balance(address: str) -> Decimal | None:
    # TON uses a query param (safer) but apply a basic sanity check anyway.
    if not _TON_ADDRESS_RE.match(address):
        logger.warning("rejecting malformed TON address")
        return None
    data = await _get_json(
        "https://toncenter.com/api/v2/getAddressBalance", {"address": address}
    )
    if not data.get("ok"):
        return None
    nanotons = Decimal(str(data.get("result")))
    return nanotons / Decimal(10) ** 9


_FETCHERS = {"BTC": _btc_balance, "ETH": _eth_balance, "TON": _ton_balance}


async def fetch_balance(chain: str, address: str) -> Optional[Decimal]:
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
        # In production we never fabricate a balance — fail soft with None so the
        # caller can skip the wallet rather than persist a fake/zero asset.
        return None


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
