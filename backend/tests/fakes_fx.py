"""Offline stand-ins for the public FX providers.

Payload shapes mirror the live APIs (checked 2026-10-14):

* Frankfurter  ``GET https://api.frankfurter.dev/v1/{date}?from=EUR&to=USD``
  -> ``{"amount": 1.0, "base": "EUR", "date": "2024-03-28", "rates": {"USD": 1.08}}``
  (weekend / holiday -> the last published day in ``date``; unknown -> 404)
* NBRB         ``GET https://api.nbrb.by/exrates/rates/USD?parammode=2&ondate=...``
  -> ``{"Cur_Scale": 1, "Date": "2024-03-29T00:00:00", "Cur_OfficialRate": 3.2498}``
* NBG          ``GET https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/?currencies=USD&date=...``
  -> ``[{"date": "2024-03-29T00:00:00.000Z", "currencies": [{"code": "USD", "quantity": 1, "rate": 2.69}]}]``
  (no data -> ``[]``)
* CBR RF       ``GET https://www.cbr.ru/scripts/XML_daily.asp?date_req=30/03/2024``
  -> windows-1251 XML ``<ValCurs Date="30.03.2024"><Valute><CharCode>USD</CharCode>
  <Nominal>1</Nominal><Value>92,3660</Value>...`` -- the rate IN FORCE on any requested
  date (weekend / holiday -> the day it was set, reported in ``Date``).

Tests never touch the network: ``FakeFx`` replaces ``market._http_get_json`` and
``market._http_get_bytes``.
"""
from __future__ import annotations

import asyncio
import re
from decimal import Decimal

import httpx

_FRANKFURTER = re.compile(r"^https://api\.frankfurter\.dev/v1/(\d{4}-\d{2}-\d{2})$")
_CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
_NBRB = "https://api.nbrb.by/exrates/rates/USD"
_NBG = "https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/"

ECB_CURRENCIES = frozenset(
    "AUD BGN BRL CAD CHF CNY CZK DKK EUR GBP HKD HUF IDR ILS INR ISK JPY KRW MXN MYR NOK "
    "NZD PHP PLN RON SEK SGD THB TRY ZAR".split()
)


def _not_found(url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        "404 Not Found", request=request, response=httpx.Response(404, request=request)
    )


def _float(value: str | Decimal) -> float:
    return float(Decimal(str(value)))


def cbr_xml(on: str, rub_per_usd: str, nominal: int = 1) -> bytes:
    """A realistic XML_daily.asp document (windows-1251, decimal comma)."""
    year, month, day = on.split("-")
    value = f"{Decimal(str(rub_per_usd)):.4f}".replace(".", ",")
    document = (
        '<?xml version="1.0" encoding="windows-1251"?>'
        f'<ValCurs Date="{day}.{month}.{year}" name="Foreign Currency Market">'
        '<Valute ID="R01010"><NumCode>036</NumCode><CharCode>AUD</CharCode><Nominal>1</Nominal>'
        "<Name>Австралийский доллар</Name><Value>60,3335</Value><VunitRate>60,3335</VunitRate></Valute>"
        f'<Valute ID="R01235"><NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>{nominal}</Nominal>'
        f"<Name>Доллар США</Name><Value>{value}</Value><VunitRate>{value}</VunitRate></Valute>"
        "</ValCurs>"
    )
    return document.encode("windows-1251")


class FakeFx:
    """Programmable provider fake; ``calls`` records ``provider:ccy:requested-date``."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._ecb: dict[tuple[str, str], tuple[str, str]] = {}
        self._nbrb: dict[str, tuple[str, str, int]] = {}
        self._nbg: dict[str, tuple[str, str, int]] = {}
        self._cbr: dict[str, tuple[str, str, int]] = {}
        self._raise: Exception | None = None
        self._hang = False
        self._garbage = False

    # --- programming -------------------------------------------------------

    def ecb(self, ccy: str, on: str, usd_per_unit: str, actual: str | None = None) -> None:
        self._ecb[(ccy, on)] = (str(usd_per_unit), actual or on)

    def nbrb(self, on: str, byn_per_usd: str, actual: str | None = None, scale: int = 1) -> None:
        self._nbrb[on] = (str(byn_per_usd), actual or on, scale)

    def nbg(self, on: str, gel_per_usd: str, actual: str | None = None, quantity: int = 1) -> None:
        self._nbg[on] = (str(gel_per_usd), actual or on, quantity)

    def cbr(self, on: str, rub_per_usd: str, actual: str | None = None, nominal: int = 1) -> None:
        """The Bank of Russia answers ``on`` with the rate in force since ``actual``."""
        self._cbr[on] = (str(rub_per_usd), actual or on, nominal)

    def set(self, ccy: str, on: str, usd_per_unit: str) -> None:
        """Provider-agnostic: route ``ccy`` to its real provider with a USD-per-unit value."""
        usd = Decimal(str(usd_per_unit))
        if ccy == "BYN":
            self.nbrb(on, str(Decimal(1) / usd))
        elif ccy == "GEL":
            self.nbg(on, str(Decimal(1) / usd))
        elif ccy == "RUB":
            self.cbr(on, str(Decimal(1) / usd))
        else:
            assert ccy in ECB_CURRENCIES, f"no fake provider for {ccy}"
            self.ecb(ccy, on, str(usd))

    def fail_all(self, exc: Exception | None = None) -> None:
        self._raise = exc or httpx.ConnectError("offline")

    def hang(self) -> None:
        self._hang = True

    def garbage(self) -> None:
        """Every provider answers 200 with an unexpected shape."""
        self._garbage = True

    def recover(self) -> None:
        """Back to normal service (whatever was programmed stays)."""
        self._raise = None
        self._hang = False
        self._garbage = False

    @property
    def call_count(self) -> int:
        return len(self.calls)

    # --- failure modes shared by both entry points ---------------------------------

    async def _misbehave(self, url: str) -> bool:
        """Apply a programmed failure; returns True when the answer is garbage."""
        if self._hang:
            self.calls.append(f"hang:{url}")
            await asyncio.sleep(3600)
        if self._raise is not None:
            self.calls.append(f"error:{url}")
            raise self._raise
        if self._garbage:
            self.calls.append(f"garbage:{url}")
            return True
        return False

    # --- the patched ``market._http_get_json`` ---------------------------------

    async def __call__(self, url: str, params: dict | None = None):
        params = params or {}
        if await self._misbehave(url):
            return {"unexpected": ["shape"]}

        m = _FRANKFURTER.match(url)
        if m:
            return self._frankfurter(url, m.group(1), params)
        if url == _NBRB:
            return self._nbrb_answer(url, params)
        if url == _NBG:
            return self._nbg_answer(params)
        raise AssertionError(f"unexpected provider URL in tests: {url} {params}")

    # --- the patched ``market._http_get_bytes`` ----------------------------------

    async def get_bytes(self, url: str, params: dict | None = None, max_bytes: int | None = None):
        params = params or {}
        if await self._misbehave(url):
            return b"<html><body>maintenance</body></html>"
        if url != _CBR_URL:
            raise AssertionError(f"unexpected provider URL in tests: {url} {params}")
        day, month, year = params["date_req"].split("/")
        on = f"{year}-{month}-{day}"
        self.calls.append(f"cbr:RUB:{on}")
        hit = self._cbr.get(on)
        if hit is None:
            raise _not_found(url)
        rate, actual, nominal = hit
        return cbr_xml(actual, rate, nominal)

    # --- provider answers ---------------------------------------------------------

    def _frankfurter(self, url: str, on: str, params: dict):
        ccy = params["from"]
        assert params["to"] == "USD"
        self.calls.append(f"ecb:{ccy}:{on}")
        hit = self._ecb.get((ccy, on))
        if hit is None:
            raise _not_found(url)
        rate, actual = hit
        return {"amount": 1.0, "base": ccy, "date": actual, "rates": {"USD": _float(rate)}}

    def _nbrb_answer(self, url: str, params: dict):
        on = params["ondate"]
        assert params.get("parammode") == "2"
        self.calls.append(f"nbrb:BYN:{on}")
        hit = self._nbrb.get(on)
        if hit is None:
            raise _not_found(url)
        rate, actual, scale = hit
        return {
            "Cur_ID": 431,
            "Date": f"{actual}T00:00:00",
            "Cur_Abbreviation": "USD",
            "Cur_Scale": scale,
            "Cur_OfficialRate": _float(rate),
        }

    def _nbg_answer(self, params: dict):
        on = params["date"]
        assert params["currencies"] == "USD"
        self.calls.append(f"nbg:GEL:{on}")
        hit = self._nbg.get(on)
        if hit is None:
            return []
        rate, actual, quantity = hit
        return [
            {
                "date": f"{actual}T00:00:00.000Z",
                "currencies": [{"code": "USD", "quantity": quantity, "rate": _float(rate)}],
            }
        ]
