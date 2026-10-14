"""What the FX providers' HTTP helpers retry, what they treat as final, and how big
an answer they accept -- run against httpx.MockTransport with the REAL tenacity
retry policy (nothing is patched out except the network itself).
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from core.db import SessionLocal
from models.finance import FxRateDaily
from services import fx_history, market
from services.fx_history import FxBudget
from tests.fakes_fx import cbr_xml

# Captured at import time, i.e. before the autouse fixture swaps them for offline stubs.
REAL_GET_JSON = market._http_get_json
REAL_GET_BYTES = market._http_get_bytes

URL = "https://provider.test/rates"
FRI = date(2024, 3, 29)


class Wire:
    """A scripted server: the n-th request gets the n-th step (the last one repeats)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.script: list[httpx.Response | Exception] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.script[min(len(self.requests), len(self.script)) - 1]
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture
def wire(monkeypatch) -> Wire:
    server = Wire()
    transport = httpx.MockTransport(server.handler)
    monkeypatch.setattr(
        market, "_new_client", lambda: httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(10.0))
    )
    monkeypatch.setattr(market, "_http_get_json", REAL_GET_JSON)
    monkeypatch.setattr(market, "_http_get_bytes", REAL_GET_BYTES)
    return server


def _ok(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


# --- JSON helper: retries ------------------------------------------------------------


@pytest.mark.parametrize("code", [400, 401, 403, 404, 410, 429])
async def test_a_client_error_is_final_and_asked_once(wire, code):
    wire.script = [httpx.Response(code)]
    started = time.monotonic()

    with pytest.raises(httpx.HTTPStatusError) as caught:
        await market._http_get_json(URL)

    assert caught.value.response.status_code == code
    assert len(wire.requests) == 1
    assert time.monotonic() - started < 0.4  # no backoff sleeps


async def test_a_server_error_is_retried_until_the_provider_recovers(wire):
    wire.script = [httpx.Response(500), httpx.Response(502), _ok({"rates": {"USD": 1.1}})]
    started = time.monotonic()

    data = await market._http_get_json(URL)

    assert data == {"rates": {"USD": 1.1}}
    assert len(wire.requests) == 3
    assert time.monotonic() - started >= 1.4  # the real 0.5 s + 1.0 s backoff


async def test_network_errors_are_retried(wire):
    wire.script = [httpx.ConnectError("down"), httpx.ReadTimeout("slow"), _ok({"ok": True})]

    assert await market._http_get_json(URL) == {"ok": True}
    assert len(wire.requests) == 3


async def test_a_provider_that_keeps_failing_is_tried_three_times_then_given_up(wire):
    wire.script = [httpx.Response(503)]

    with pytest.raises(httpx.HTTPStatusError) as caught:
        await market._http_get_json(URL)

    assert caught.value.response.status_code == 503
    assert len(wire.requests) == 3


# --- bytes helper -----------------------------------------------------------------------


async def test_bytes_helper_returns_the_body_and_sends_the_params(wire):
    wire.script = [httpx.Response(200, content=b"<xml/>")]

    body = await market._http_get_bytes(URL, {"date_req": "30/03/2024"}, max_bytes=1000)

    assert body == b"<xml/>"
    assert wire.requests[0].url.params["date_req"] == "30/03/2024"


async def test_bytes_helper_follows_the_same_retry_policy(wire):
    wire.script = [httpx.Response(404)]
    with pytest.raises(httpx.HTTPStatusError):
        await market._http_get_bytes(URL, max_bytes=1000)
    assert len(wire.requests) == 1

    wire.requests.clear()
    wire.script = [httpx.ConnectError("down"), httpx.Response(200, content=b"fine")]
    assert await market._http_get_bytes(URL, max_bytes=1000) == b"fine"
    assert len(wire.requests) == 2


async def test_bytes_helper_refuses_an_oversized_declared_body_without_retrying(wire):
    wire.script = [httpx.Response(200, content=b"x" * 2000)]  # Content-Length: 2000

    with pytest.raises(ValueError):
        await market._http_get_bytes(URL, max_bytes=1000)

    assert len(wire.requests) == 1


async def test_bytes_helper_stops_reading_an_undeclared_oversized_body(wire):
    async def endless():
        for _ in range(10_000):
            yield b"x" * 1024  # no Content-Length: only counting the stream can catch it

    wire.script = [httpx.Response(200, content=endless())]

    with pytest.raises(ValueError):
        await market._http_get_bytes(URL, max_bytes=4096)


async def test_redirects_are_not_followed(wire):
    wire.script = [httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})]

    with pytest.raises(httpx.HTTPStatusError):
        await market._http_get_bytes(URL, max_bytes=1000)

    assert len(wire.requests) == 1  # never went to the redirect target


# --- through the rate chain ----------------------------------------------------------------


async def _quote(ccy: str, on: date, **kw):
    async with SessionLocal() as db:
        quote = await fx_history.usd_per_unit_on(db, ccy, on, **kw)
        await fx_history.flush_rates(db)
        await db.commit()
        return quote


async def test_a_provider_404_is_one_request_and_means_no_data(db_ready, wire):
    wire.script = [httpx.Response(404, json={"message": "not found"})]
    started = time.monotonic()

    assert await _quote("EUR", FRI) is None

    assert len(wire.requests) == 1
    assert time.monotonic() - started < 0.4


async def test_a_dead_provider_costs_at_most_the_time_budget_and_gives_no_rate(db_ready, wire):
    wire.script = [httpx.Response(500)]
    started = time.monotonic()

    quote = await _quote("EUR", FRI)  # default budget: <= 4 s

    elapsed = time.monotonic() - started
    assert quote is None
    assert len(wire.requests) == 3  # tried, retried, gave up -- on its own, inside the budget
    assert elapsed < 4.5


async def test_the_budget_cuts_retries_short(db_ready, wire):
    wire.script = [httpx.Response(500)]
    started = time.monotonic()

    quote = await _quote("EUR", FRI, budget=FxBudget(0.7))

    assert quote is None
    assert len(wire.requests) <= 2
    assert time.monotonic() - started < 1.5


async def test_frankfurter_answer_over_the_real_helper(db_ready, wire):
    wire.script = [_ok({"amount": 1.0, "base": "EUR", "date": "2024-03-28", "rates": {"USD": 1.0811}})]

    quote = await _quote("EUR", FRI)

    assert quote.rate == Decimal("1.0811") and quote.rate_date == date(2024, 3, 28)
    assert wire.requests[0].url.path == "/v1/2024-03-29"
    assert dict(wire.requests[0].url.params) == {"from": "EUR", "to": "USD"}


async def test_cbr_official_xml_over_the_real_helper(db_ready, wire):
    wire.script = [httpx.Response(200, content=cbr_xml("2024-03-30", "92.3660"))]

    quote = await _quote("RUB", date(2024, 3, 30))

    assert quote.rate == (Decimal(1) / Decimal("92.3660")).quantize(Decimal("0.0000000001"))
    assert quote.rate_date == date(2024, 3, 30) and quote.source == "cbr"
    request = wire.requests[0]
    assert (request.url.host, request.url.path) == ("www.cbr.ru", "/scripts/XML_daily.asp")
    assert request.url.params["date_req"] == "30/03/2024"
    assert len(wire.requests) == 1


async def test_a_provider_404_still_allows_the_stored_fallback(db_ready, wire):
    async with SessionLocal() as db:
        db.add(
            FxRateDaily(
                rate_date=FRI - timedelta(days=3),
                currency="EUR",
                usd_per_unit=Decimal("1.05"),
                source="ecb",
                fetched_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    wire.script = [httpx.Response(404)]

    quote = await _quote("EUR", FRI)

    assert quote.rate == Decimal("1.05") and quote.rate_date == FRI - timedelta(days=3)
    assert len(wire.requests) == 1
