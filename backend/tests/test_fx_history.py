"""Historical FX (services/fx_history): rate on a date for a currency, offline.

Resolution order under test: fx_rates_daily -> provider (and save) -> nearest
earlier rate within 7 days -> (today only) the ordinary current rate -> no rate.
All provider traffic is a programmable fake (tests/fakes_fx.py); nothing here
touches the network.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text


from core.db import SessionLocal
from models.finance import FxRateDaily
from services import fx_history, market
from services.fx_history import FxBudget

FRI = date(2024, 3, 29)
SAT = date(2024, 3, 30)
SUN = date(2024, 3, 31)
MON = date(2024, 4, 1)


def _today() -> date:
    return datetime.now(timezone.utc).date()


async def _quote(ccy: str, on: date, **kw):
    async with SessionLocal() as db:
        quote = await fx_history.usd_per_unit_on(db, ccy, on, **kw)
        await fx_history.flush_rates(db)
        await db.commit()
        return quote


async def _between(ccy: str, base: str, on: date, **kw):
    async with SessionLocal() as db:
        out = await fx_history.rate_between(db, ccy, base, on, **kw)
        await fx_history.flush_rates(db)
        await db.commit()
        return out


async def _rows() -> list[FxRateDaily]:
    async with SessionLocal() as db:
        return list(await db.scalars(select(FxRateDaily).order_by(FxRateDaily.rate_date)))


async def _seed(ccy: str, on: date, usd: str, source: str = "ecb") -> None:
    async with SessionLocal() as db:
        db.add(
            FxRateDaily(
                rate_date=on,
                currency=ccy,
                usd_per_unit=Decimal(usd),
                source=source,
                fetched_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()


# --- pure helpers ------------------------------------------------------------


def test_budget_default_fits_the_jarvis_proxy_timeout():
    # Jarvis' proxy gives up after 5 s; the whole external-rate path must stay under 4.
    assert fx_history.FX_BUDGET_SECONDS <= 4.0


@pytest.mark.parametrize(
    ("amount", "rate", "expected"),
    [
        ("10.005", "1", "10.01"),          # half-up, not banker's
        ("2.675", "1", "2.68"),            # classic float trap
        ("-10.005", "1", "-10.01"),        # refunds round away from zero too
        ("100", "0.3322", "33.22"),
        ("50", "3.2498", "162.49"),        # 162.49 exactly
        ("1", "3.0098", "3.01"),
        ("-0.001", "1", "0.00"),           # never "-0.00"
    ],
)
def test_apply_rate_rounds_half_up_to_cents(amount, rate, expected):
    out = fx_history.apply_rate(Decimal(amount), Decimal(rate))
    assert str(out) == expected


# --- USD and identity ----------------------------------------------------------


async def test_usd_is_one_without_any_lookup(db_ready, fake_fx):
    quote = await _quote("USD", FRI)
    assert quote.rate == 1 and quote.source is None and quote.rate_date == FRI
    assert fake_fx.call_count == 0
    assert await _rows() == []


async def test_rate_between_same_currency_is_identity(db_ready, fake_fx):
    conv = await _between("BYN", "BYN", FRI)
    assert conv.rate == 1 and conv.source is None
    assert fake_fx.call_count == 0


# --- ECB (Frankfurter) ---------------------------------------------------------


async def test_ecb_currency_uses_frankfurter_for_the_date_and_saves_it(db_ready, fake_fx):
    fake_fx.ecb("EUR", "2024-03-29", "1.0811")

    quote = await _quote("EUR", FRI)

    assert (quote.rate, quote.rate_date, quote.source) == (Decimal("1.0811"), FRI, "ecb")
    assert fake_fx.calls == ["ecb:EUR:2024-03-29"]
    rows = await _rows()
    assert len(rows) == 1
    assert (rows[0].rate_date, rows[0].currency, rows[0].source) == (FRI, "EUR", "ecb")
    assert rows[0].usd_per_unit == Decimal("1.0811")


async def test_second_lookup_is_served_from_the_table_without_http(db_ready, fake_fx):
    fake_fx.ecb("EUR", "2024-03-29", "1.0811")
    await _quote("EUR", FRI)
    fake_fx.calls.clear()
    # Drop the in-process cache so only fx_rates_daily can answer.
    from core.cache import get_cache

    get_cache()._store.clear()

    quote = await _quote("EUR", FRI)

    assert quote.rate == Decimal("1.0811") and quote.source == "ecb"
    assert fake_fx.call_count == 0


async def test_weekend_request_reports_the_actual_business_day(db_ready, fake_fx):
    fake_fx.ecb("GBP", "2024-03-31", "1.2643", actual="2024-03-28")

    quote = await _quote("GBP", SUN)

    assert quote.rate_date == date(2024, 3, 28)  # the real date goes into fx_date
    assert quote.source == "ecb"
    assert [r.rate_date for r in await _rows()] == [date(2024, 3, 28)]


async def test_weekend_repeat_does_not_hit_the_provider_again(db_ready, fake_fx):
    fake_fx.ecb("GBP", "2024-03-31", "1.2643", actual="2024-03-28")
    await _quote("GBP", SUN)
    fake_fx.calls.clear()

    quote = await _quote("GBP", SUN)

    assert quote.rate_date == date(2024, 3, 28)
    assert fake_fx.call_count == 0  # in-process cache keyed by the requested date


async def test_provider_is_tried_before_an_older_stored_rate(db_ready, fake_fx):
    await _seed("EUR", date(2024, 3, 25), "1.0000")
    fake_fx.ecb("EUR", "2024-03-29", "1.0811")

    quote = await _quote("EUR", FRI)

    assert quote.rate == Decimal("1.0811") and quote.rate_date == FRI


async def test_ecb_before_its_history_starts_is_not_requested(db_ready, fake_fx):
    assert await _quote("EUR", date(1998, 12, 31)) is None
    assert fake_fx.call_count == 0


# --- NBRB (BYN) ----------------------------------------------------------------


async def test_byn_uses_nbrb_for_the_date(db_ready, fake_fx):
    fake_fx.nbrb("2024-03-29", "3.2498")

    quote = await _quote("BYN", FRI)

    assert quote.source == "nbrb" and quote.rate_date == FRI
    assert quote.rate == (Decimal(1) / Decimal("3.2498")).quantize(Decimal("0.0000000001"))
    assert fake_fx.calls == ["nbrb:BYN:2024-03-29"]


async def test_byn_honours_the_rate_scale(db_ready, fake_fx):
    fake_fx.nbrb("2024-03-29", "325", scale=100)  # 325 BYN per 100 USD -> 3.25 per USD

    quote = await _quote("BYN", FRI)

    assert quote.rate == (Decimal(1) / Decimal("3.25")).quantize(Decimal("0.0000000001"))


async def test_byn_before_the_2016_redenomination_gets_no_rate(db_ready, fake_fx):
    # NBRB answers pre-2016 dates in old (10 000x) roubles: never mix them with BYN.
    assert await _quote("BYN", date(2016, 6, 30)) is None
    assert fake_fx.call_count == 0


# --- NBG (GEL) -----------------------------------------------------------------


async def test_gel_uses_nbg_for_the_date(db_ready, fake_fx):
    fake_fx.nbg("2024-03-29", "2.6978")

    quote = await _quote("GEL", FRI)

    assert quote.source == "nbg" and quote.rate_date == FRI
    assert quote.rate == (Decimal(1) / Decimal("2.6978")).quantize(Decimal("0.0000000001"))
    assert fake_fx.calls == ["nbg:GEL:2024-03-29"]


async def test_gel_empty_answer_means_no_rate(db_ready, fake_fx):
    assert await _quote("GEL", FRI) is None  # NBG answers [] for dates it has no data for


async def test_gel_answer_dated_after_the_request_is_rejected(db_ready, fake_fx):
    # NBG silently answers "today" for out-of-range dates; a future rate is not
    # a rate for the expense date.
    fake_fx.nbg("2024-03-29", "2.70", actual="2024-03-30")
    assert await _quote("GEL", FRI) is None


# --- CBR RF (RUB): the official XML_daily.asp ------------------------------------------


async def test_rub_uses_the_central_bank_for_the_date(db_ready, fake_fx):
    fake_fx.cbr("2024-03-29", "92.2628")

    quote = await _quote("RUB", FRI)

    assert quote.source == "cbr" and quote.rate_date == FRI
    assert quote.rate == (Decimal(1) / Decimal("92.2628")).quantize(Decimal("0.0000000001"))
    assert fake_fx.calls == ["cbr:RUB:2024-03-29"]


async def test_rub_honours_the_nominal(db_ready, fake_fx):
    fake_fx.cbr("2024-03-29", "9226.28", nominal=100)
    quote = await _quote("RUB", FRI)
    assert quote.rate == (Decimal(100) / Decimal("9226.28")).quantize(Decimal("0.0000000001"))


async def test_rub_on_a_saturday_is_the_rate_in_force_that_day(db_ready, fake_fx):
    fake_fx.cbr("2024-03-30", "92.366")  # the Bank of Russia answers any calendar day

    quote = await _quote("RUB", SAT)

    assert quote.rate == (Decimal(1) / Decimal("92.366")).quantize(Decimal("0.0000000001"))
    assert (quote.rate_date, quote.source) == (SAT, "cbr")
    assert fake_fx.calls == ["cbr:RUB:2024-03-30"]  # no walking back, one request


async def test_rub_in_the_new_year_holidays_gets_the_rate_set_before_them(db_ready, fake_fx):
    fake_fx.cbr("2024-01-05", "89.6883", actual="2023-12-30")

    quote = await _quote("RUB", date(2024, 1, 5))

    assert quote.rate_date == date(2023, 12, 30)
    assert fake_fx.calls == ["cbr:RUB:2024-01-05"]


async def test_a_provider_answer_in_force_is_not_subject_to_the_stored_rate_age_limit(db_ready, fake_fx):
    # 10 days old by the calendar, yet it is *the* rate in force on that date.
    fake_fx.cbr("2024-01-09", "89.6883", actual="2023-12-30")

    quote = await _quote("RUB", date(2024, 1, 9))

    assert quote.rate_date == date(2023, 12, 30) and quote.source == "cbr"


async def test_rub_answer_dated_after_the_request_is_rejected(db_ready, fake_fx):
    fake_fx.cbr("2024-03-29", "92.2628", actual="2024-03-30")
    assert await _quote("RUB", FRI) is None


def _cbr_bytes(monkeypatch, payload: bytes) -> list[dict]:
    seen: list[dict] = []

    async def _bytes(url, params=None, max_bytes=None):
        seen.append({"url": url, "params": params, "max_bytes": max_bytes})
        return payload

    monkeypatch.setattr(market, "_http_get_bytes", _bytes)
    return seen


async def test_rub_parses_windows_1251_xml_with_a_decimal_comma(db_ready, monkeypatch):
    from tests.fakes_fx import cbr_xml

    seen = _cbr_bytes(monkeypatch, cbr_xml("2024-03-30", "92.3660"))

    quote = await _quote("RUB", SAT)

    assert quote.rate == (Decimal(1) / Decimal("92.366")).quantize(Decimal("0.0000000001"))
    assert seen[0]["url"] == "https://www.cbr.ru/scripts/XML_daily.asp"
    assert seen[0]["params"] == {"date_req": "30/03/2024"}
    assert 0 < seen[0]["max_bytes"] <= 1_000_000  # the answer size is capped


_VALUTE_USD = b"<Valute><CharCode>USD</CharCode><Nominal>%s</Nominal><Value>%s</Value></Valute>"


@pytest.mark.parametrize(
    "payload",
    [
        b"<html><body>maintenance</body></html>",  # wrong document
        b"<ValCurs Date='30.03.2024'><Valute><CharCode>EUR</CharCode><Nominal>1</Nominal>"
        b"<Value>99,5</Value></Valute></ValCurs>",  # no USD
        b"<ValCurs Date='30.03.2024'>" + _VALUTE_USD % (b"1", b"abc") + b"</ValCurs>",  # unreadable value
        b"<ValCurs Date='30.03.2024'>" + _VALUTE_USD % (b"0", b"92,3") + b"</ValCurs>",  # nominal 0
        b"<ValCurs Date='30.03.2024'>" + _VALUTE_USD % (b"1", b"0,0000") + b"</ValCurs>",  # zero rate
        b"<ValCurs>" + _VALUTE_USD % (b"1", b"92,3") + b"</ValCurs>",  # no Date
        b"<ValCurs Date='soon'>" + _VALUTE_USD % (b"1", b"92,3") + b"</ValCurs>",  # bad Date
        b"<ValCurs Date='30.03.2024'><Valute>",  # truncated
        b"",  # empty
        b"\xff\xfe not xml at all",
    ],
)
async def test_rub_rejects_unusable_xml_as_a_provider_failure(db_ready, monkeypatch, payload):
    _cbr_bytes(monkeypatch, payload)
    assert await _quote("RUB", SAT) is None


_BODY = "<ValCurs Date='30.03.2024'><Valute><CharCode>USD</CharCode><Nominal>1</Nominal><Value>{}</Value></Valute></ValCurs>"


@pytest.mark.parametrize(
    "document",
    [
        # Each would parse into a perfectly good USD rate if DTDs were allowed: only the
        # DOCTYPE guard can be the reason for the refusal.
        ("<?xml version='1.0'?><!DOCTYPE x [<!ENTITY u '92,3'>]>" + _BODY.format("&u;")).encode(),
        ("<?xml version='1.0'?><!DOCTYPE ValCurs>" + _BODY.format("92,3")).encode(),
        ("<?xml version='1.0'?><!DOCTYPE x SYSTEM 'http://169.254.169.254/evil.dtd'>" + _BODY.format("92,3")).encode(),
        ("<?xml version='1.0' encoding='utf-16'?><!DOCTYPE ValCurs>" + _BODY.format("92,3")).encode("utf-16"),
        (
            "<?xml version='1.0'?><!DOCTYPE lolz [<!ENTITY a 'aaaaaaaaaa'><!ENTITY b '&a;&a;&a;&a;&a;&a;&a;&a;'>]>"
            + _BODY.format("&b;")
        ).encode(),
    ],
)
async def test_rub_never_parses_xml_with_a_doctype_or_entities(db_ready, monkeypatch, document):
    _cbr_bytes(monkeypatch, document)
    assert await _quote("RUB", SAT) is None


async def test_the_same_documents_parse_fine_without_the_doctype(db_ready, monkeypatch):
    _cbr_bytes(monkeypatch, _BODY.format("92,3").encode())  # the control case for the test above
    quote = await _quote("RUB", SAT)
    assert quote.rate == (Decimal(1) / Decimal("92.3")).quantize(Decimal("0.0000000001"))


# --- no provider ---------------------------------------------------------------


async def test_currency_without_a_provider_gets_no_rate_for_a_past_date(db_ready, fake_fx, monkeypatch):
    async def _latest(ccy: str):
        raise AssertionError("the current rate must not stand in for a past date")

    monkeypatch.setattr(market, "usd_rate_for_currency", _latest)

    assert await _quote("KZT", FRI) is None
    assert fake_fx.call_count == 0


# --- nearest earlier rate ------------------------------------------------------


async def test_nearest_earlier_stored_rate_within_seven_days_is_used(db_ready, fake_fx):
    await _seed("EUR", FRI - timedelta(days=7), "1.0500")  # the provider has nothing for FRI

    quote = await _quote("EUR", FRI)

    assert quote.rate == Decimal("1.05") and quote.source == "ecb"
    assert quote.rate_date == FRI - timedelta(days=7)  # its own date, not the requested one


async def test_nearest_earlier_prefers_the_closest_row(db_ready, fake_fx):
    await _seed("EUR", FRI - timedelta(days=6), "1.0100")
    await _seed("EUR", FRI - timedelta(days=2), "1.0200")

    quote = await _quote("EUR", FRI)

    assert quote.rate == Decimal("1.02") and quote.rate_date == FRI - timedelta(days=2)


async def test_rate_older_than_seven_days_is_not_used(db_ready, fake_fx):
    await _seed("EUR", FRI - timedelta(days=8), "1.0500")
    assert await _quote("EUR", FRI) is None


async def test_a_later_stored_rate_is_never_used_for_an_earlier_date(db_ready, fake_fx):
    await _seed("EUR", FRI + timedelta(days=1), "1.0500")
    assert await _quote("EUR", FRI) is None


async def test_provider_answer_is_the_rate_in_force_however_old_its_date(db_ready, fake_fx):
    # The 7-day limit is only for the *stored* fallback; a provider that answers a
    # date answers with the rate in force on it.
    fake_fx.ecb("EUR", "2024-03-29", "1.08", actual="2024-03-10")

    quote = await _quote("EUR", FRI)

    assert quote.rate == Decimal("1.08") and quote.rate_date == date(2024, 3, 10)


# --- provider failures never raise ---------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("down"),
        httpx.ReadTimeout("slow"),
        ValueError("not json"),
    ],
)
async def test_provider_transport_errors_degrade_to_no_rate(db_ready, fake_fx, error):
    fake_fx.fail_all(error)
    assert await _quote("EUR", FRI) is None
    assert await _quote("BYN", FRI) is None


async def test_provider_http_500_degrades_to_no_rate(db_ready, fake_fx):
    request = httpx.Request("GET", "https://example.invalid")
    fake_fx.fail_all(
        httpx.HTTPStatusError("500", request=request, response=httpx.Response(500, request=request))
    )
    assert await _quote("GEL", FRI) is None
    assert await _quote("RUB", FRI) is None


async def test_unexpected_payload_shape_degrades_to_no_rate(db_ready, fake_fx):
    fake_fx.garbage()
    for ccy in ("EUR", "BYN", "GEL", "RUB"):
        assert await _quote(ccy, FRI) is None


# --- today: the ordinary current rate ------------------------------------------


async def test_today_falls_back_to_the_current_rate_when_the_provider_has_no_data(db_ready, fake_fx, monkeypatch):
    seen: list[str] = []

    async def _latest(ccy: str):
        seen.append(ccy)
        return Decimal("1.1500")

    monkeypatch.setattr(market, "usd_rate_for_currency", _latest)
    today = _today()  # the fake provider knows nothing about today: 404

    quote = await _quote("EUR", today)

    assert (quote.rate, quote.rate_date, quote.source) == (Decimal("1.15"), today, "latest")
    assert seen == ["EUR"]


async def test_today_prefers_the_provider_over_the_current_rate(db_ready, fake_fx, monkeypatch):
    async def _latest(ccy: str):
        raise AssertionError("provider answered; the current rate must not be asked")

    monkeypatch.setattr(market, "usd_rate_for_currency", _latest)
    today = _today()
    fake_fx.ecb("EUR", today.isoformat(), "1.12", actual=(today - timedelta(days=1)).isoformat())

    quote = await _quote("EUR", today)

    assert quote.source == "ecb" and quote.rate_date == today - timedelta(days=1)


async def test_tomorrow_is_priced_with_todays_provider_rate(db_ready, fake_fx):
    today = _today()
    fake_fx.ecb("EUR", today.isoformat(), "1.12")

    quote = await _quote("EUR", today + timedelta(days=1))

    assert quote.rate_date == today and quote.source == "ecb"
    assert fake_fx.calls == [f"ecb:EUR:{today.isoformat()}"]


async def test_today_without_any_rate_is_none(db_ready, fake_fx):
    assert await _quote("KZT", _today()) is None  # offline market returns None too


# --- time budget -----------------------------------------------------------------


async def test_a_hanging_provider_is_cut_off_by_the_budget(db_ready, fake_fx):
    fake_fx.hang()
    started = time.monotonic()

    quote = await _quote("EUR", FRI, budget=FxBudget(0.1))

    assert quote is None
    assert time.monotonic() - started < 1.5


async def test_an_expired_budget_skips_the_network_but_not_the_table(db_ready, fake_fx):
    await _seed("EUR", FRI, "1.0811")
    budget = FxBudget(0.0)

    stored = await _quote("EUR", FRI, budget=budget)
    missing = await _quote("GBP", FRI, budget=budget)

    assert stored.rate == Decimal("1.0811")
    assert missing is None
    assert fake_fx.call_count == 0


async def test_the_default_budget_is_set_per_lookup_chain(db_ready, fake_fx, monkeypatch):
    monkeypatch.setattr(fx_history, "FX_BUDGET_SECONDS", 0.1)
    fake_fx.hang()
    started = time.monotonic()
    assert await _between("EUR", "BYN", FRI) is None
    assert time.monotonic() - started < 1.5


# --- pivot between two currencies ---------------------------------------------


async def test_rate_between_pivots_through_usd(db_ready, fake_fx):
    fake_fx.set("EUR", "2024-03-29", "1.10")
    fake_fx.set("BYN", "2024-03-29", "0.25")

    conv = await _between("EUR", "BYN", FRI)

    assert conv.rate == Decimal("4.4")  # 1.10 / 0.25
    assert conv.rate_date == FRI and conv.source == "ecb"


async def test_rate_between_into_usd_and_out_of_it(db_ready, fake_fx):
    fake_fx.set("EUR", "2024-03-29", "1.10")

    to_usd = await _between("EUR", "USD", FRI)
    from_usd = await _between("USD", "EUR", FRI)

    assert to_usd.rate == Decimal("1.1") and to_usd.source == "ecb"
    assert from_usd.rate == (Decimal(1) / Decimal("1.10")).quantize(Decimal("0.0000000001"))
    assert from_usd.source == "ecb"


async def test_rate_between_reports_the_older_leg_date(db_ready, fake_fx):
    fake_fx.ecb("EUR", "2024-03-29", "1.10", actual="2024-03-28")
    fake_fx.set("BYN", "2024-03-29", "0.25")

    conv = await _between("EUR", "BYN", FRI)

    assert conv.rate_date == date(2024, 3, 28)


async def test_rate_between_is_none_when_any_leg_is_missing(db_ready, fake_fx):
    fake_fx.set("EUR", "2024-03-29", "1.10")
    assert await _between("EUR", "KZT", FRI) is None
    assert await _between("KZT", "EUR", FRI) is None


async def test_rate_between_marks_the_result_latest_if_any_leg_is(db_ready, fake_fx, monkeypatch):
    today = _today().isoformat()
    fake_fx.set("EUR", today, "1.10")  # BYN has no dated rate today ...

    async def _latest(ccy: str):
        return Decimal("0.2500") if ccy == "BYN" else None

    monkeypatch.setattr(market, "usd_rate_for_currency", _latest)  # ... only a current one

    conv = await _between("EUR", "BYN", _today())

    assert conv.rate == Decimal("4.4") and conv.source == "latest"


# --- the rate cache is an optimisation: its failures never lose a rate -------------


async def test_an_unavailable_process_cache_does_not_break_lookups(db_ready, fake_fx, monkeypatch):
    class _BrokenCache:
        async def get(self, key):
            raise ConnectionError("redis is down")

        async def set(self, key, value, ttl):
            raise ConnectionError("redis is down")

        async def delete(self, key):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(fx_history, "get_cache", lambda: _BrokenCache())
    fake_fx.ecb("EUR", "2024-03-29", "1.0811")

    quote = await _quote("EUR", FRI)

    assert quote.rate == Decimal("1.0811") and quote.source == "ecb"


# --- a provider FAILURE is never papered over; only "no data" falls back ----------------


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("down"),
        httpx.ReadTimeout("slow"),
        httpx.HTTPStatusError(
            "500",
            request=httpx.Request("GET", "https://x.test"),
            response=httpx.Response(500, request=httpx.Request("GET", "https://x.test")),
        ),
    ],
)
async def test_provider_failure_gives_no_rate_even_with_a_fresh_stored_one(db_ready, fake_fx, error):
    await _seed("EUR", FRI - timedelta(days=1), "1.0500")  # fresh and tempting
    fake_fx.fail_all(error)

    assert await _quote("EUR", FRI) is None


async def test_provider_garbage_gives_no_rate_even_with_a_fresh_stored_one(db_ready, fake_fx):
    await _seed("EUR", FRI - timedelta(days=1), "1.0500")
    fake_fx.garbage()
    assert await _quote("EUR", FRI) is None


async def test_provider_timeout_gives_no_rate_even_with_a_fresh_stored_one(db_ready, fake_fx):
    await _seed("EUR", FRI - timedelta(days=1), "1.0500")
    fake_fx.hang()
    assert await _quote("EUR", FRI, budget=FxBudget(0.1)) is None


async def test_provider_that_could_not_be_asked_in_time_gives_no_rate_too(db_ready, fake_fx):
    await _seed("EUR", FRI - timedelta(days=1), "1.0500")
    assert await _quote("EUR", FRI, budget=FxBudget(0.0)) is None
    assert fake_fx.call_count == 0


async def test_the_stored_rate_is_the_answer_only_once_the_provider_says_no_data(db_ready, fake_fx):
    await _seed("EUR", FRI - timedelta(days=1), "1.0500")
    fake_fx.fail_all()
    assert await _quote("EUR", FRI) is None

    fake_fx.recover()  # the provider is back and simply has nothing for that date (404)
    quote = await _quote("EUR", FRI)

    assert quote.rate == Decimal("1.05") and quote.rate_date == FRI - timedelta(days=1)


async def test_today_after_a_provider_failure_does_not_reach_for_the_current_rate(db_ready, fake_fx, monkeypatch):
    async def _latest(ccy: str):
        raise AssertionError("a failed provider must not be replaced by a stale 'current' rate")

    monkeypatch.setattr(market, "usd_rate_for_currency", _latest)
    fake_fx.fail_all()

    assert await _quote("EUR", _today()) is None


async def test_a_stored_exact_rate_still_answers_while_the_provider_is_down(db_ready, fake_fx):
    await _seed("EUR", FRI, "1.0811")
    fake_fx.fail_all()

    quote = await _quote("EUR", FRI)

    assert quote.rate == Decimal("1.0811") and fake_fx.call_count == 0


# --- "no data" is remembered for ten minutes (failures are not) --------------------------


async def test_no_data_answer_is_remembered_so_the_provider_is_not_asked_again(db_ready, fake_fx):
    assert fx_history.NO_DATA_TTL == 600  # ten minutes

    assert await _quote("EUR", FRI) is None
    assert await _quote("EUR", FRI) is None
    assert await _quote("EUR", date(2024, 3, 28)) is None  # another date: asked

    assert fake_fx.calls == ["ecb:EUR:2024-03-29", "ecb:EUR:2024-03-28"]


async def test_the_no_data_memory_expires_after_its_ttl(db_ready, fake_fx):
    from core.cache import get_cache

    await _quote("EUR", FRI)
    cache = get_cache()
    [key] = [k for k in cache._store if k.startswith("fxd:nodata:")]
    expires_at, _ = cache._store[key]
    assert 590 < expires_at - time.monotonic() <= 600


async def test_a_provider_failure_is_not_remembered(db_ready, fake_fx):
    fake_fx.fail_all()
    assert await _quote("EUR", FRI) is None
    fake_fx.recover()
    fake_fx.ecb("EUR", "2024-03-29", "1.0811")

    quote = await _quote("EUR", FRI)

    assert quote.rate == Decimal("1.0811")


async def test_remembered_no_data_still_allows_the_stored_fallback(db_ready, fake_fx):
    await _quote("EUR", FRI)  # remembers "no data"
    await _seed("EUR", FRI - timedelta(days=2), "1.0200")

    quote = await _quote("EUR", FRI)

    assert quote.rate == Decimal("1.02")
    assert fake_fx.call_count == 1


# --- rates are stored in the caller's own session ----------------------------------------


async def test_fetched_rates_commit_with_the_callers_session_and_not_before(db_ready, fake_fx):
    fake_fx.ecb("EUR", "2024-03-29", "1.0811")

    async with SessionLocal() as db:
        quote = await fx_history.usd_per_unit_on(db, "EUR", FRI)
        await fx_history.flush_rates(db)

        assert quote.rate == Decimal("1.0811")
        assert await _rows() == []  # not committed: visible to nobody else yet
        await db.commit()

    assert [(r.currency, r.rate_date) for r in await _rows()] == [("EUR", FRI)]


async def test_fetched_rates_vanish_with_a_rolled_back_session(db_ready, fake_fx):
    fake_fx.ecb("EUR", "2024-03-29", "1.0811")

    async with SessionLocal() as db:
        await fx_history.usd_per_unit_on(db, "EUR", FRI)
        await fx_history.flush_rates(db)
        await db.rollback()

    assert await _rows() == []


async def test_flushing_twice_or_racing_another_writer_never_fails(db_ready, fake_fx):
    fake_fx.ecb("EUR", "2024-03-29", "1.0811")
    async with SessionLocal() as db:
        await fx_history.usd_per_unit_on(db, "EUR", FRI)
        await _seed("EUR", FRI, "1.0811")  # someone else stored it first
        await fx_history.flush_rates(db)
        await fx_history.flush_rates(db)  # nothing left to do
        await db.commit()

    assert len(await _rows()) == 1


async def test_a_lookup_that_never_flushes_writes_nothing(db_ready, fake_fx):
    fake_fx.ecb("EUR", "2024-03-29", "1.0811")
    async with SessionLocal() as db:
        await fx_history.usd_per_unit_on(db, "EUR", FRI)
        await db.commit()
    assert await _rows() == []


async def test_lookups_before_the_flush_hold_no_write_lock(db_ready, fake_fx, monkeypatch):
    """SQLite: resolving a second currency (which may write through market's own
    session) must not wait on a lock the first lookup took -- staging defers the INSERT."""
    fake_fx.set("EUR", _today().isoformat(), "1.10")

    async def _latest(ccy: str):
        async with SessionLocal() as other:  # what market._persist_fx does
            await other.execute(text("CREATE TABLE IF NOT EXISTS _probe (x int)"))
            await other.execute(text("INSERT INTO _probe VALUES (1)"))
            await other.commit()
        return None

    monkeypatch.setattr(market, "usd_rate_for_currency", _latest)
    started = time.monotonic()

    async with SessionLocal() as db:
        await fx_history.rate_between(db, "EUR", "KZT", _today())  # EUR found, KZT -> latest
        await fx_history.flush_rates(db)
        await db.commit()

    assert time.monotonic() - started < 3  # a held lock would wait out SQLite's 5 s timeout


# --- BGN has ECB history ----------------------------------------------------------------


async def test_bgn_is_served_by_the_ecb_for_dates_it_has(db_ready, fake_fx):
    fake_fx.ecb("BGN", "2025-06-02", "0.58385")

    quote = await _quote("BGN", date(2025, 6, 2))

    assert quote.rate == Decimal("0.58385") and quote.source == "ecb"
    assert fake_fx.calls == ["ecb:BGN:2025-06-02"]


async def test_an_explicit_retry_ignores_the_no_data_memory(db_ready, fake_fx):
    # revalue is the user's "ask again": a remembered "no data" must not block it.
    assert await _quote("EUR", FRI) is None  # remembered
    fake_fx.ecb("EUR", "2024-03-29", "1.0811")

    assert await _quote("EUR", FRI) is None  # an ordinary lookup still trusts the memory
    quote = await _quote("EUR", FRI, retry_no_data=True)

    assert quote.rate == Decimal("1.0811")
    assert await _quote("EUR", FRI) is not None  # the answer is cached for everyone now
