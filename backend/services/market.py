"""Market data service — FX rates and crypto prices, in USD.

Sources (all keyless/public for dev):
  - frankfurter.app  : ECB fiat cross-rates (EUR, GBP, RUB-removed, etc.)
  - api.nbrb.by      : National Bank of Belarus -> BYN
  - nbg.gov.ge       : National Bank of Georgia -> GEL
  - api.coingecko.com: crypto spot prices

Layered resolution per lookup:
  1. in-memory TTL cache (fast path, dev)
  2. live HTTP (httpx + tenacity retry/backoff)
  3. DB fallback (last good value in exchange_rates / asset_prices)

Every value is a ``Decimal``. A failed lookup returns ``None`` (callers degrade
gracefully — the asset is saved without a USD value rather than crashing).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import select
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.cache import get_cache
from core.config import settings
from core.db import SessionLocal
from models.market import AssetPrice, ExchangeRate

logger = logging.getLogger("kapital.market")

# TTLs (seconds)
_FX_TTL = 3600          # fiat rates ~hourly
_CRYPTO_TTL = 300       # crypto ~5 min
_STOCK_TTL = 900        # equities ~15 min

_HTTP_TIMEOUT = httpx.Timeout(10.0)

# CoinGecko ids for the symbols we support.
_COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "USDT": "tether",
    "USDC": "usd-coin",
    "BNB": "binancecoin",
    "SOL": "solana",
    "XRP": "ripple",
    "TON": "the-open-network",
    "TRX": "tron",
}

# National-bank overrides for currencies frankfurter doesn't carry.
_NATIONAL_BANK = {"BYN", "GEL"}

_retry = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    retry=retry_if_exception_type((httpx.HTTPError,)),
)


def _to_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


# --- HTTP fetchers (raw) ------------------------------------------------------


@_retry
async def _http_get_json(url: str, params: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def _fetch_fx_frankfurter(ccy: str) -> Decimal | None:
    """1 unit of `ccy` in USD via ECB (frankfurter)."""
    data = await _http_get_json(
        "https://api.frankfurter.dev/v1/latest", {"from": ccy, "to": "USD"}
    )
    return _to_decimal((data.get("rates") or {}).get("USD"))


async def _fetch_fx_nbrb(ccy: str) -> Decimal | None:
    """BYN -> USD via National Bank of Belarus.

    NBRB returns the value of 1 (scaled) USD in BYN; invert to get BYN->USD.
    """
    if ccy != "BYN":
        return None
    data = await _http_get_json(
        "https://api.nbrb.by/exrates/rates/USD", {"parammode": "2"}
    )
    rate = _to_decimal(data.get("Cur_OfficialRate"))   # BYN per scale USD
    scale = _to_decimal(data.get("Cur_Scale")) or Decimal(1)
    if not rate or rate == 0:
        return None
    byn_per_usd = rate / scale
    return (Decimal(1) / byn_per_usd) if byn_per_usd else None


async def _fetch_fx_nbg(ccy: str) -> Decimal | None:
    """GEL -> USD via National Bank of Georgia."""
    if ccy != "GEL":
        return None
    data = await _http_get_json(
        "https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/",
        {"currencies": "USD"},
    )
    # Response: [{"currencies": [{"code": "USD", "rate": 2.7, "quantity": 1}]}]
    try:
        entry = data[0]["currencies"][0]
    except (KeyError, IndexError, TypeError):
        return None
    rate = _to_decimal(entry.get("rate"))       # GEL per `quantity` USD
    quantity = _to_decimal(entry.get("quantity")) or Decimal(1)
    if not rate or rate == 0:
        return None
    gel_per_usd = rate / quantity
    return (Decimal(1) / gel_per_usd) if gel_per_usd else None


async def _fetch_crypto_coingecko(symbol: str) -> Decimal | None:
    coin_id = _COINGECKO_IDS.get(symbol.upper())
    if not coin_id:
        return None
    data = await _http_get_json(
        "https://api.coingecko.com/api/v3/simple/price",
        {"ids": coin_id, "vs_currencies": "usd"},
    )
    return _to_decimal((data.get(coin_id) or {}).get("usd"))


# Demo prices for a handful of popular tickers, so equities show a value with
# no provider key (DEMO_MODE / no keys). Clearly approximate, never live.
_DEMO_STOCK_PRICES = {
    "AAPL": "230", "MSFT": "430", "GOOGL": "175", "AMZN": "190", "TSLA": "250",
    "NVDA": "120", "META": "560", "SPY": "560", "VOO": "510", "QQQ": "480",
    "VWCE": "120", "VUSA": "100", "BRK.B": "450", "NFLX": "700", "AMD": "150",
}


async def _fetch_stock_alphavantage(ticker: str) -> Decimal | None:
    if not settings.alpha_vantage_api_key:
        return None
    data = await _http_get_json(
        "https://www.alphavantage.co/query",
        {
            "function": "GLOBAL_QUOTE",
            "symbol": ticker,
            "apikey": settings.alpha_vantage_api_key,
        },
    )
    return _to_decimal((data.get("Global Quote") or {}).get("05. price"))


async def _fetch_stock_finnhub(ticker: str) -> Decimal | None:
    if not settings.finnhub_api_key:
        return None
    data = await _http_get_json(
        "https://finnhub.io/api/v1/quote",
        {"symbol": ticker, "token": settings.finnhub_api_key},
    )
    price = _to_decimal(data.get("c"))  # current price
    return price if price and price > 0 else None


# --- DB persistence + fallback ------------------------------------------------


async def _persist_fx(ccy: str, rate: Decimal, source: str) -> None:
    async with SessionLocal() as s:
        row = await s.scalar(
            select(ExchangeRate).where(
                ExchangeRate.from_currency == ccy, ExchangeRate.to_currency == "USD"
            )
        )
        if row is None:
            s.add(
                ExchangeRate(
                    from_currency=ccy, to_currency="USD", rate=rate, source=source,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        else:
            row.rate = rate
            row.source = source
            row.fetched_at = datetime.now(timezone.utc)
        await s.commit()


# Stored FX is only trusted as a fallback if refreshed within this window.
_FX_FALLBACK_MAX_AGE = timedelta(days=7)


async def _fallback_fx(ccy: str) -> Decimal | None:
    async with SessionLocal() as s:
        row = await s.scalar(
            select(ExchangeRate).where(
                ExchangeRate.from_currency == ccy, ExchangeRate.to_currency == "USD"
            )
        )
    if row is None or row.rate is None or row.rate <= 0:
        return None
    fetched = row.fetched_at
    if fetched is None:
        return None
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - fetched > _FX_FALLBACK_MAX_AGE:
        return None
    return row.rate


async def _persist_crypto(symbol: str, price: Decimal, source: str) -> None:
    async with SessionLocal() as s:
        row = await s.scalar(
            select(AssetPrice).where(
                AssetPrice.symbol == symbol, AssetPrice.asset_class == "crypto"
            )
        )
        if row is None:
            s.add(
                AssetPrice(
                    symbol=symbol, asset_class="crypto", price_usd=price, source=source,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        else:
            row.price_usd = price
            row.source = source
            row.fetched_at = datetime.now(timezone.utc)
        await s.commit()


async def _fallback_crypto(symbol: str) -> Decimal | None:
    async with SessionLocal() as s:
        row = await s.scalar(
            select(AssetPrice).where(
                AssetPrice.symbol == symbol, AssetPrice.asset_class == "crypto"
            )
        )
        return row.price_usd if row else None


# --- Public API ---------------------------------------------------------------


async def usd_rate_for_currency(ccy: str) -> Decimal | None:
    """Return the value of 1 unit of `ccy` in USD (Decimal), or None."""
    ccy = (ccy or "").strip().upper()
    if not ccy:
        return None
    if ccy == "USD":
        return Decimal(1)

    cache = get_cache()
    cache_key = f"fx:{ccy}:USD"
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached

    rate: Decimal | None = None
    source = ""
    try:
        if ccy in _NATIONAL_BANK:
            rate = await (_fetch_fx_nbrb(ccy) if ccy == "BYN" else _fetch_fx_nbg(ccy))
            source = "nbrb" if ccy == "BYN" else "nbg"
        else:
            rate = await _fetch_fx_frankfurter(ccy)
            source = "frankfurter"
    except httpx.HTTPError as e:
        logger.warning("FX fetch failed for %s: %s", ccy, e)

    if rate and rate > 0:
        await cache.set(cache_key, rate, _FX_TTL)
        try:
            await _persist_fx(ccy, rate, source)
        except Exception as e:  # persistence is best-effort
            logger.warning("FX persist failed for %s: %s", ccy, e)
        return rate

    # Fallback: last good DB value.
    fallback = await _fallback_fx(ccy)
    if fallback:
        await cache.set(cache_key, fallback, _FX_TTL)
        logger.info("FX fallback (cache/DB) used for %s", ccy)
    return fallback


async def usd_price_for_crypto(symbol: str) -> Decimal | None:
    """Return the USD spot price for one unit of a crypto `symbol`, or None."""
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return None

    cache = get_cache()
    cache_key = f"crypto:{symbol}:USD"
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached

    price: Decimal | None = None
    try:
        price = await _fetch_crypto_coingecko(symbol)
    except httpx.HTTPError as e:
        logger.warning("Crypto fetch failed for %s: %s", symbol, e)

    if price and price > 0:
        await cache.set(cache_key, price, _CRYPTO_TTL)
        try:
            await _persist_crypto(symbol, price, "coingecko")
        except Exception as e:
            logger.warning("Crypto persist failed for %s: %s", symbol, e)
        return price

    fallback = await _fallback_crypto(symbol)
    if fallback:
        await cache.set(cache_key, fallback, _CRYPTO_TTL)
        logger.info("Crypto fallback (cache/DB) used for %s", symbol)
    return fallback


async def usd_per_unit(base_ccy: str) -> Decimal | None:
    """How many USD = 1 unit of ``base_ccy`` (for display normalisation). 1 for USD."""
    base_ccy = (base_ccy or "USD").strip().upper()
    if base_ccy == "USD":
        return Decimal(1)
    return await usd_rate_for_currency(base_ccy)


async def usd_price_for_stock(ticker: str) -> Decimal | None:
    """USD price for one share of ``ticker`` (Alpha Vantage → Finnhub → DB), or None.

    With no provider key (demo) we fall back to an approximate canned price for
    popular tickers so equities aren't left blank during testing.
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None

    cache = get_cache()
    cache_key = f"stock:{ticker}:USD"
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached

    price: Decimal | None = None
    # Each provider gets its own try so an Alpha Vantage failure still lets
    # Finnhub run before we fall through to the DB/demo fallback.
    try:
        price = await _fetch_stock_alphavantage(ticker)
    except httpx.HTTPError as e:
        logger.warning("Stock fetch (Alpha Vantage) failed for %s: %s", ticker, e)
    if price is None or price <= 0:
        try:
            price = await _fetch_stock_finnhub(ticker)
        except httpx.HTTPError as e:
            logger.warning("Stock fetch (Finnhub) failed for %s: %s", ticker, e)

    if price and price > 0:
        await cache.set(cache_key, price, _STOCK_TTL)
        try:
            await _persist_stock(ticker, price, "alphavantage/finnhub")
        except Exception as e:  # noqa: BLE001
            logger.warning("Stock persist failed for %s: %s", ticker, e)
        return price

    # DB fallback (last good live value), else demo canned price.
    fallback = await _fallback_stock(ticker)
    if fallback:
        await cache.set(cache_key, fallback, _STOCK_TTL)
        return fallback

    if settings.is_demo and ticker in _DEMO_STOCK_PRICES:
        demo_price = _to_decimal(_DEMO_STOCK_PRICES[ticker])
        if demo_price is not None:
            await cache.set(cache_key, demo_price, _STOCK_TTL)
            return demo_price
    return None


async def _persist_stock(ticker: str, price: Decimal, source: str) -> None:
    async with SessionLocal() as s:
        row = await s.scalar(
            select(AssetPrice).where(
                AssetPrice.symbol == ticker, AssetPrice.asset_class == "stock"
            )
        )
        if row is None:
            s.add(
                AssetPrice(
                    symbol=ticker, asset_class="stock", price_usd=price, source=source,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        else:
            row.price_usd = price
            row.source = source
            row.fetched_at = datetime.now(timezone.utc)
        await s.commit()


async def _fallback_stock(ticker: str) -> Decimal | None:
    async with SessionLocal() as s:
        row = await s.scalar(
            select(AssetPrice).where(
                AssetPrice.symbol == ticker, AssetPrice.asset_class == "stock"
            )
        )
        return row.price_usd if row else None


# --- Scheduler warmers (called by APScheduler dev / Celery prod) --------------

# A small set worth keeping warm for a dev portfolio. Extended dynamically as
# users add assets (slice-8 will query distinct held currencies/symbols).
_WARM_CURRENCIES = ["EUR", "GBP", "BYN", "GEL", "RUB", "PLN", "KZT", "UAH"]
_WARM_CRYPTO = ["BTC", "ETH", "USDT", "SOL"]


async def refresh_fx() -> None:
    for ccy in _WARM_CURRENCIES:
        try:
            await usd_rate_for_currency(ccy)
        except Exception as e:  # noqa: BLE001 — scheduler job must not crash
            logger.warning("refresh_fx %s failed: %s", ccy, e)


async def refresh_crypto() -> None:
    for sym in _WARM_CRYPTO:
        try:
            await usd_price_for_crypto(sym)
        except Exception as e:  # noqa: BLE001
            logger.warning("refresh_crypto %s failed: %s", sym, e)
