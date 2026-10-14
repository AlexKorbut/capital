"""Historical FX — "how much USD was 1 unit of X worth on this date" (fin-01).

Resolution order for one (currency, date):

  1. in-process cache (keyed by the *requested* date, so weekends do not re-fetch)
  2. ``fx_rates_daily`` row for that exact date
  3. the national/ECB provider for that date; a rate it answers is staged for storage
  4. ONLY if the provider answered "no data for that date": the nearest *earlier*
     stored rate, at most ``STALENESS_DAYS`` old (with its own date)
  5. ONLY if the provider answered "no data" and the date is today/tomorrow: the
     ordinary current rate (``market``), source ``latest``
  6. nothing -> ``None`` (the caller stores the expense "without a rate")

A provider *failure* (timeout, network, 5xx, malformed answer, no time left) is not
"no data": it gives ``None`` straight away -- no quiet stand-in from the table, no
"current" rate -- so the expense is saved "without a rate" and ``revalue`` completes
it once the provider is back. Only a definite "I have nothing for that date" (404,
empty list) is remembered, for ``NO_DATA_TTL`` seconds.

Providers (all keyless, all through the ``market`` HTTP helpers; 4xx is never retried):

  ecb   Frankfurter ``/v1/{date}``  -- 31 ECB currencies; weekend -> last business day
  nbrb  api.nbrb.by                 -- BYN, only from the 2016 redenomination on
  nbg   nbg.gov.ge                  -- GEL
  cbr   www.cbr.ru XML_daily.asp    -- RUB, the official rate IN FORCE on any date
                                       (weekends, holidays included); windows-1251 XML

A provider answers with the rate in force on the date and the date it is valid from;
that answer is never aged out (the 7-day limit is only for the stored fallback).

All external requests of one API call share a wall-clock ``FxBudget`` (<= 4 s): the
host proxy (Jarvis) gives up after 5 s.

Storage: a fetched rate is *staged* on the caller's session and written by
``flush_rates`` (INSERT .. ON CONFLICT DO NOTHING) in that same session, so it commits
or rolls back with the caller's transaction -- no second pooled connection. Flushing is
deferred until all lookups are done, so on SQLite no write lock is held while a lookup
(``market`` writes through its own session) is still running. Callers must therefore
call ``flush_rates(db)`` after their lookups and before they commit.

All values are ``Decimal``. Logs carry currencies and dates only -- never amounts.
"""
from __future__ import annotations

import asyncio
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Awaitable, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.cache import get_cache
from models.finance import FxRateDaily
from services import market

logger = logging.getLogger("kapital.fx_history")

# Total wall-clock allowance for every external request made on behalf of one API call.
FX_BUDGET_SECONDS = 4.0
# How old a *stored* rate may be to stand in for a date the provider has no data for.
STALENESS_DAYS = 7
# "The provider has nothing for that currency+date" is remembered this long (seconds).
NO_DATA_TTL = 600

CENT = Decimal("0.01")
RATE_QUANT = Decimal("0.0000000001")  # fx_rates_daily / fx_rate carry 10 decimals

_CACHE_TTL_PAST = 24 * 3600
_CACHE_TTL_TODAY = 3600

ECB_FIRST_DAY = date(1999, 1, 4)
BYN_FIRST_DAY = date(2016, 7, 1)  # earlier NBRB rates are in pre-redenomination roubles
CBR_MAX_BYTES = 512 * 1024  # the real document is ~8 KB

# Currencies Frankfurter serves (ECB reference rates). Kept in code so an unsupported
# currency never costs a doomed request. BGN has history up to the euro changeover
# (later dates answer 404 = no data); RUB left the ECB set in 2022 (-> Bank of Russia).
ECB_CURRENCIES = frozenset(
    "AUD BGN BRL CAD CHF CNY CZK DKK EUR GBP HKD HUF IDR ILS INR ISK JPY KRW MXN MYR NOK "
    "NZD PHP PLN RON SEK SGD THB TRY ZAR".split()
)

_FRANKFURTER_URL = "https://api.frankfurter.dev/v1/{day}"
_NBRB_URL = "https://api.nbrb.by/exrates/rates/USD"
_NBG_URL = "https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/"
_CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"

# What counts as "the provider failed" (never as "no data"): transport trouble, 5xx and
# other unexpected statuses, answers of the wrong shape, unreadable numbers/dates/XML.
_PROVIDER_ERRORS = (
    httpx.HTTPError,
    ValueError,
    KeyError,
    IndexError,
    TypeError,
    AttributeError,
    ArithmeticError,
    ET.ParseError,
)

_STAGED_KEY = "fx_history.staged"


@dataclass(frozen=True)
class RateQuote:
    """A rate with the date it is actually valid for and where it came from.

    From ``usd_per_unit_on`` ``rate`` is USD per 1 unit of the currency; from
    ``rate_between`` it is base-currency units per 1 unit of the currency.
    ``source`` is ``None`` when no lookup was needed (USD, identity).
    """

    rate: Decimal
    rate_date: date
    source: str | None


@dataclass(frozen=True)
class _Lookup:
    """Outcome of asking a provider: a quote, "no data" (default), or a failure."""

    quote: RateQuote | None = None
    failed: bool = False


class FxBudget:
    """One wall-clock allowance shared by all external requests of an API call."""

    def __init__(self, seconds: float | None = None) -> None:
        allowance = FX_BUDGET_SECONDS if seconds is None else seconds
        self._deadline = time.monotonic() + allowance

    def remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    @property
    def expired(self) -> bool:
        return self.remaining() <= 0.0


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def apply_rate(amount: Decimal, rate: Decimal) -> Decimal:
    """``amount * rate`` rounded half-up to cents (never ``-0.00``)."""
    value = (amount * rate).quantize(CENT, rounding=ROUND_HALF_UP)
    return Decimal("0.00") if value == 0 else value


# --- providers ----------------------------------------------------------------------------
# A fetcher returns (usd_per_unit, the date that rate is valid from), or None for
# "no data for that date"; anything else it raises counts as a provider failure.

_Found = tuple[Decimal, date]


def _day(value: object) -> date:
    return date.fromisoformat(str(value)[:10])


def _positive(value: object) -> Decimal:
    number = market._to_decimal(value)
    if number is None or not number.is_finite() or number <= 0:
        raise ValueError("expected a positive number")
    return number


async def _json_or_none(url: str, params: dict | None = None) -> Any | None:
    """JSON body, or ``None`` when the provider says 404 ("nothing for that date")."""
    try:
        return await market._http_get_json(url, params)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise


async def _fetch_ecb(ccy: str, on: date) -> _Found | None:
    data = await _json_or_none(_FRANKFURTER_URL.format(day=on.isoformat()), {"from": ccy, "to": "USD"})
    if data is None:
        return None
    return _positive(data["rates"]["USD"]), _day(data["date"])


async def _fetch_nbrb(ccy: str, on: date) -> _Found | None:
    data = await _json_or_none(_NBRB_URL, {"parammode": "2", "ondate": on.isoformat()})
    if data is None:
        return None
    byn_per_scale = _positive(data["Cur_OfficialRate"])  # BYN per `scale` USD
    scale = _positive(data["Cur_Scale"])
    return scale / byn_per_scale, _day(data["Date"])


async def _fetch_nbg(ccy: str, on: date) -> _Found | None:
    data = await _json_or_none(_NBG_URL, {"currencies": "USD", "date": on.isoformat()})
    if data is None or data == []:  # [] = no data for that date
        return None
    block = data[0]
    entry = block["currencies"][0]
    gel_per_quantity = _positive(entry["rate"])  # GEL per `quantity` USD
    quantity = _positive(entry["quantity"])
    return quantity / gel_per_quantity, _day(block["date"])


class _NoDoctypeBuilder(ET.TreeBuilder):
    """Refuses any DOCTYPE the moment expat reports it -- before an entity can be declared."""

    def doctype(self, name, pubid, system) -> None:  # noqa: D401 -- ElementTree hook
        raise ValueError("DTD/entities are not accepted in provider XML")


def _parse_cbr(document: bytes) -> _Found:
    """USD from the Bank of Russia's XML_daily.asp (stdlib parser, hardened)."""
    # No DOCTYPE at all => no entity declarations, no external DTD: nothing to expand.
    parser = ET.XMLParser(target=_NoDoctypeBuilder())
    parser.feed(document)
    root = parser.close()
    if root.tag != "ValCurs":
        raise ValueError("unexpected document")
    valid_from = datetime.strptime(root.attrib["Date"], "%d.%m.%Y").date()
    for valute in root.iter("Valute"):
        if valute.findtext("CharCode") == "USD":
            nominal = _positive(valute.findtext("Nominal"))
            rub_per_nominal = _positive((valute.findtext("Value") or "").replace(",", "."))
            return nominal / rub_per_nominal, valid_from  # RUB per `nominal` USD, inverted
    raise ValueError("no USD entry")


async def _fetch_cbr(ccy: str, on: date) -> _Found | None:
    # One request: the Bank answers any calendar date with the rate in force on it and
    # the date it was set (a Saturday gets Saturday's; 5 Jan gets the one from 30 Dec).
    try:
        document = await market._http_get_bytes(
            _CBR_URL, {"date_req": on.strftime("%d/%m/%Y")}, max_bytes=CBR_MAX_BYTES
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise
    return _parse_cbr(document)


_Fetcher = Callable[[str, date], Awaitable["_Found | None"]]


def _provider_for(ccy: str, on: date) -> tuple[str, _Fetcher] | None:
    if ccy == "BYN":
        return ("nbrb", _fetch_nbrb) if on >= BYN_FIRST_DAY else None
    if ccy == "GEL":
        return "nbg", _fetch_nbg
    if ccy == "RUB":
        return "cbr", _fetch_cbr
    if ccy in ECB_CURRENCIES and on >= ECB_FIRST_DAY:
        return "ecb", _fetch_ecb
    return None


# --- storage ---------------------------------------------------------------------------------


def _as_quote(row: FxRateDaily) -> RateQuote:
    return RateQuote(row.usd_per_unit, row.rate_date, row.source)


async def _stored_exact(db: AsyncSession, ccy: str, on: date) -> RateQuote | None:
    row = await db.scalar(
        select(FxRateDaily).where(FxRateDaily.currency == ccy, FxRateDaily.rate_date == on)
    )
    return _as_quote(row) if row else None


async def _stored_before(db: AsyncSession, ccy: str, on: date) -> RateQuote | None:
    row = await db.scalar(
        select(FxRateDaily)
        .where(
            FxRateDaily.currency == ccy,
            FxRateDaily.rate_date < on,
            FxRateDaily.rate_date >= on - timedelta(days=STALENESS_DAYS),
        )
        .order_by(FxRateDaily.rate_date.desc())
        .limit(1)
    )
    return _as_quote(row) if row else None


def _stage(db: AsyncSession, ccy: str, quote: RateQuote) -> None:
    db.sync_session.info.setdefault(_STAGED_KEY, {})[(ccy, quote.rate_date)] = quote


async def flush_rates(db: AsyncSession) -> None:
    """Write the rates fetched on ``db`` into ``fx_rates_daily`` -- in ``db``'s own
    transaction (no commit here). Insert-if-absent, so a concurrent writer is harmless."""
    staged: dict[tuple[str, date], RateQuote] | None = db.sync_session.info.pop(_STAGED_KEY, None)
    if not staged:
        return
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    now = datetime.now(timezone.utc)
    rows = [
        {
            "date": quote.rate_date,
            "currency": ccy,
            "usd_per_unit": quote.rate,
            "source": quote.source,
            "fetched_at": now,
        }
        for (ccy, _), quote in staged.items()
    ]
    await db.execute(
        insert(FxRateDaily.__table__).values(rows).on_conflict_do_nothing(index_elements=["date", "currency"])
    )


# --- in-process cache (an optimisation: its failures degrade to a miss) ------------------------


def _cache_key(ccy: str, on: date) -> str:
    return f"fxd:{ccy}:{on.isoformat()}"


def _no_data_key(ccy: str, on: date) -> str:
    return f"fxd:nodata:{ccy}:{on.isoformat()}"


async def _cache_get(ccy: str, on: date) -> RateQuote | None:
    try:
        raw = await get_cache().get(_cache_key(ccy, on))
        if not raw:
            return None
        return RateQuote(raw["rate"], date.fromisoformat(raw["date"]), raw["source"])
    except Exception as exc:  # noqa: BLE001 -- any cache fault degrades to a miss
        logger.warning("FX cache read failed (%s)", type(exc).__name__)
        return None


async def _cache_put(ccy: str, on: date, quote: RateQuote) -> None:
    ttl = _CACHE_TTL_TODAY if on >= today_utc() else _CACHE_TTL_PAST
    value = {"rate": quote.rate, "date": quote.rate_date.isoformat(), "source": quote.source}
    try:
        await get_cache().set(_cache_key(ccy, on), value, ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("FX cache write failed (%s)", type(exc).__name__)


async def _no_data_known(ccy: str, on: date) -> bool:
    try:
        return bool(await get_cache().get(_no_data_key(ccy, on)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("FX cache read failed (%s)", type(exc).__name__)
        return False


async def _remember_no_data(ccy: str, on: date) -> None:
    try:
        await get_cache().set(_no_data_key(ccy, on), True, NO_DATA_TTL)
    except Exception as exc:  # noqa: BLE001
        logger.warning("FX cache write failed (%s)", type(exc).__name__)


# --- lookup ------------------------------------------------------------------------------------


async def _from_provider(
    db: AsyncSession, ccy: str, on: date, budget: FxBudget, retry_no_data: bool
) -> _Lookup:
    spec = _provider_for(ccy, on)
    if spec is None or (not retry_no_data and await _no_data_known(ccy, on)):
        return _Lookup()
    source, fetch = spec
    if budget.expired:  # could not even ask: that is a failure, not "no data"
        return _Lookup(failed=True)
    try:
        async with asyncio.timeout(budget.remaining()):
            found = await fetch(ccy, on)
    except TimeoutError:
        logger.warning("FX %s on %s: %s timed out", ccy, on, source)
        return _Lookup(failed=True)
    except _PROVIDER_ERRORS as exc:
        logger.warning("FX %s on %s: %s failed (%s)", ccy, on, source, type(exc).__name__)
        return _Lookup(failed=True)
    if found is None:
        await _remember_no_data(ccy, on)
        return _Lookup()
    usd_per_unit, rate_date = found
    rate = usd_per_unit.quantize(RATE_QUANT, rounding=ROUND_HALF_UP)
    if rate <= 0 or rate_date > on:  # a rate from the future is not a rate for this date
        logger.warning("FX %s on %s: %s answered an unusable rate/date", ccy, on, source)
        return _Lookup(failed=True)
    quote = RateQuote(rate, rate_date, source)
    _stage(db, ccy, quote)
    return _Lookup(quote)


async def _latest(ccy: str, today: date, budget: FxBudget) -> RateQuote | None:
    if budget.expired:
        return None
    try:
        async with asyncio.timeout(budget.remaining()):
            rate = await market.usd_rate_for_currency(ccy)
    except TimeoutError:
        logger.warning("FX %s: current-rate lookup timed out", ccy)
        return None
    if rate is None or rate <= 0:
        return None
    return RateQuote(rate.quantize(RATE_QUANT, rounding=ROUND_HALF_UP), today, "latest")


async def usd_per_unit_on(
    db: AsyncSession,
    currency: str,
    on: date,
    *,
    budget: FxBudget | None = None,
    retry_no_data: bool = False,
) -> RateQuote | None:
    """USD per 1 unit of ``currency`` on ``on`` (see the module docstring), or ``None``.

    ``retry_no_data`` asks the provider again even if it recently said "no data" (an
    explicit revalue). Call ``flush_rates(db)`` afterwards to store what was fetched.
    """
    ccy = currency.strip().upper()
    if ccy == "USD":
        return RateQuote(Decimal(1), on, None)
    budget = FxBudget() if budget is None else budget
    today = today_utc()
    # A rate for tomorrow does not exist yet: ask for "as of today".
    target = min(on, today)

    quote = await _cache_get(ccy, target)
    if quote is not None:
        return quote
    quote = await _stored_exact(db, ccy, target)
    if quote is not None:
        return quote
    lookup = await _from_provider(db, ccy, target, budget, retry_no_data)
    if lookup.quote is not None:
        await _cache_put(ccy, target, lookup.quote)
        return lookup.quote
    if lookup.failed:
        return None  # no quiet stand-in for a provider that failed
    quote = await _stored_before(db, ccy, target)
    if quote is not None:
        return quote
    if on >= today:
        return await _latest(ccy, today, budget)
    return None


async def rate_between(
    db: AsyncSession,
    currency: str,
    base: str,
    on: date,
    *,
    budget: FxBudget | None = None,
    retry_no_data: bool = False,
) -> RateQuote | None:
    """Base-currency units per 1 unit of ``currency`` on ``on`` (USD pivot), or ``None``.

    ``rate_date`` is the older of the legs' dates; ``source`` is the expense
    currency's provider (``latest`` if any leg had to use the current rate).
    """
    ccy, base_ccy = currency.strip().upper(), base.strip().upper()
    if ccy == base_ccy:
        return RateQuote(Decimal(1), on, None)
    budget = FxBudget() if budget is None else budget

    legs: list[RateQuote] = []
    numerator = denominator = Decimal(1)
    if ccy != "USD":
        leg = await usd_per_unit_on(db, ccy, on, budget=budget, retry_no_data=retry_no_data)
        if leg is None:
            return None
        legs.append(leg)
        numerator = leg.rate
    if base_ccy != "USD":
        leg = await usd_per_unit_on(db, base_ccy, on, budget=budget, retry_no_data=retry_no_data)
        if leg is None:
            return None
        legs.append(leg)
        denominator = leg.rate

    rate = (numerator / denominator).quantize(RATE_QUANT, rounding=ROUND_HALF_UP)
    if rate <= 0:
        return None
    source = "latest" if any(q.source == "latest" for q in legs) else legs[0].source
    return RateQuote(rate, min(q.rate_date for q in legs), source)
