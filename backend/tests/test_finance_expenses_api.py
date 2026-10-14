"""Finance expenses API: pricing at the rate of the day, validation, idempotency, edits."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from core.db import SessionLocal
from models.finance import FinExpense, FinProject
from services import fx_history
from tests.finance_client import (
    FIN,
    add_expense,
    add_ok,
    client,
    days_from_today,
    make_project,
    register,
)

FRI = "2024-03-29"
SAT = "2024-03-30"
MON = "2024-04-01"


async def _setup(c, base: str = "USD", name: str = "Машина"):
    h = await register(c)
    p = await make_project(c, h, name, base)
    return h, p


async def _expense_rows() -> list[FinExpense]:
    async with SessionLocal() as db:
        return list(await db.scalars(select(FinExpense)))


# --- pricing ---------------------------------------------------------------------


async def test_expense_in_the_base_currency_needs_no_rate(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")

        res = await add_expense(c, h, p["id"], "50", "USD", FRI)

        assert res.status_code == 201, res.text
        e = res.json()
        assert uuid.UUID(e["id"]) and e["project_id"] == p["id"]
        assert (e["amount"], e["currency"], e["spent_on"]) == ("50.00", "USD", FRI)
        assert (e["base_amount"], e["base_currency"]) == ("50.00", "USD")
        assert e["fx_rate"] == "1" and e["fx_date"] is None and e["fx_source"] is None
        assert e["category"] is None and e["note"] is None and e["client_id"] is None
        assert e["created_at"] and e["updated_at"]
        assert fake_fx.call_count == 0


async def test_foreign_expense_is_priced_at_the_rate_of_its_date(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    async with await client() as c:
        h, p = await _setup(c, "USD")

        e = await add_ok(c, h, p["id"], "100", "EUR", FRI)

        assert e["base_amount"] == "110.00"
        assert (e["fx_rate"], e["fx_date"], e["fx_source"]) == ("1.1", FRI, "ecb")


async def test_two_foreign_currencies_pivot_through_usd(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    fake_fx.set("BYN", FRI, "0.25")
    async with await client() as c:
        h, p = await _setup(c, "BYN")

        e = await add_ok(c, h, p["id"], "100", "EUR", FRI)

        assert e["base_amount"] == "440.00"
        assert e["fx_rate"] == "4.4" and e["fx_source"] == "ecb"


@pytest.mark.parametrize(
    ("currency", "usd_per_unit", "expected_usd", "source"),
    [
        ("BYN", "0.25", "25.00", "nbrb"),
        ("GEL", "0.5", "50.00", "nbg"),
        ("RUB", "0.0125", "1.25", "cbr"),
        ("GBP", "1.25", "125.00", "ecb"),
    ],
)
async def test_every_provider_reaches_the_expense(db_ready, fake_fx, currency, usd_per_unit, expected_usd, source):
    fake_fx.set(currency, FRI, usd_per_unit)
    async with await client() as c:
        h, p = await _setup(c, "USD")

        e = await add_ok(c, h, p["id"], "100", currency, FRI)

        assert (e["base_amount"], e["fx_source"], e["fx_date"]) == (expected_usd, source, FRI)


async def test_weekend_expense_carries_the_real_rate_date(db_ready, fake_fx):
    fake_fx.ecb("EUR", SAT, "1.10", actual=FRI)
    async with await client() as c:
        h, p = await _setup(c, "USD")

        e = await add_ok(c, h, p["id"], "100", "EUR", SAT)

        assert e["spent_on"] == SAT and e["fx_date"] == FRI and e["fx_source"] == "ecb"


async def test_base_amount_is_rounded_half_up(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    async with await client() as c:
        h, p = await _setup(c, "USD")

        plus = await add_ok(c, h, p["id"], "0.05", "EUR", FRI)      # 0.055 -> 0.06
        minus = await add_ok(c, h, p["id"], "-0.05", "EUR", FRI)    # -0.055 -> -0.06
        ident = await add_ok(c, h, p["id"], "0.125", "USD", FRI)    # identity: 0.125 -> 0.13

        assert (plus["base_amount"], minus["base_amount"], ident["base_amount"]) == ("0.06", "-0.06", "0.13")
        assert ident["amount"] == "0.125"  # the original amount is kept as typed


async def test_manual_rate_wins_and_needs_no_provider(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")

        e = await add_ok(c, h, p["id"], "100", "EUR", FRI, fx_rate="1.2345")

        assert e["base_amount"] == "123.45"
        assert (e["fx_rate"], e["fx_source"], e["fx_date"]) == ("1.2345", "manual", None)
        assert fake_fx.call_count == 0


async def test_manual_rate_is_ignored_when_the_currency_is_the_base(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        e = await add_ok(c, h, p["id"], "10", "USD", FRI, fx_rate="5")
        assert (e["base_amount"], e["fx_rate"], e["fx_source"]) == ("10.00", "1", None)


@pytest.mark.parametrize("rate", ["0", "-1", "1000000000", "abc", "0.00000000001", "NaN"])
async def test_manual_rate_must_be_positive_and_sane(db_ready, fake_fx, rate):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        res = await add_expense(c, h, p["id"], "10", "EUR", FRI, fx_rate=rate)
        assert res.status_code == 422, (rate, res.text)


async def test_no_rate_available_saves_the_expense_unpriced(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")

        res = await add_expense(c, h, p["id"], "5000", "KZT", FRI)

        assert res.status_code == 201, res.text
        e = res.json()
        assert e["base_amount"] is None and e["fx_rate"] is None
        assert e["fx_date"] is None and e["fx_source"] is None
        assert e["amount"] == "5000.00"


async def test_provider_outage_saves_the_expense_unpriced(db_ready, fake_fx):
    fake_fx.fail_all()
    async with await client() as c:
        h, p = await _setup(c, "USD")
        res = await add_expense(c, h, p["id"], "100", "EUR", FRI)
        assert res.status_code == 201 and res.json()["base_amount"] is None


async def test_hanging_provider_does_not_block_the_save(db_ready, fake_fx, monkeypatch):
    monkeypatch.setattr(fx_history, "FX_BUDGET_SECONDS", 0.2)
    fake_fx.hang()
    async with await client() as c:
        h, p = await _setup(c, "USD")
        started = time.monotonic()

        res = await add_expense(c, h, p["id"], "100", "EUR", FRI)

        assert time.monotonic() - started < 3
        assert res.status_code == 201 and res.json()["base_amount"] is None
    assert len(await _expense_rows()) == 1


async def test_refund_is_a_negative_amount(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        await add_ok(c, h, p["id"], "100", "USD", FRI)
        refund = await add_ok(c, h, p["id"], "-25.5", "USD", MON)
        assert (refund["amount"], refund["base_amount"]) == ("-25.50", "-25.50")
        project = (await c.get(f"{FIN}/projects/{p['id']}", headers=h)).json()
        assert project["total_base"] == "74.50"


async def test_money_travels_as_strings(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    async with await client() as c:
        h, p = await _setup(c, "USD")
        e = await add_ok(c, h, p["id"], 100.5, "EUR", FRI)  # a JSON number is accepted ...
        for key in ("amount", "base_amount", "fx_rate"):    # ... but always answered as a string
            assert isinstance(e[key], str), key
        assert e["amount"] == "100.50" and e["base_amount"] == "110.55"


# --- validation --------------------------------------------------------------------


@pytest.mark.parametrize(
    "amount",
    ["0", "0.00", "-0", None, "", "abc", "NaN", "Infinity", "-Infinity",
     "1000000000000.01", "-1000000000001", "1.1234567", "1e13", True],
)
async def test_amount_must_be_non_zero_finite_and_bounded(db_ready, fake_fx, amount):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        res = await add_expense(c, h, p["id"], amount, "USD", FRI)
        assert res.status_code == 422, (amount, res.text)


@pytest.mark.parametrize("amount", ["1000000000000", "-1000000000000", "0.000001", "-0.000001", "1.123456"])
async def test_amount_boundaries_are_accepted(db_ready, fake_fx, amount):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        res = await add_expense(c, h, p["id"], amount, "USD", FRI)
        assert res.status_code == 201, (amount, res.text)


async def test_amount_whose_base_value_would_overflow_is_rejected(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        res = await add_expense(c, h, p["id"], "1000000000000", "EUR", FRI, fx_rate="999999999")
        assert res.status_code == 422, res.text
        assert await _expense_rows() == []


@pytest.mark.parametrize("currency", ["XYZ", "US", "USDD", "12$", "", "e u", "ЕВР", "BTC", None, 5])
async def test_currency_must_be_an_iso_4217_code(db_ready, fake_fx, currency):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        res = await add_expense(c, h, p["id"], "1", currency, FRI)
        assert res.status_code == 422, (currency, res.text)


async def test_currency_is_case_insensitive_and_stored_uppercase(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    async with await client() as c:
        h, p = await _setup(c, "USD")
        e = await add_ok(c, h, p["id"], "10", "eur", FRI)
        assert e["currency"] == "EUR" and e["base_amount"] == "11.00"


async def test_spent_on_bounds(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        for ok_day in ("1970-01-01", days_from_today(0), days_from_today(1)):
            res = await add_expense(c, h, p["id"], "1", "USD", ok_day)
            assert res.status_code == 201, (ok_day, res.text)
        for bad_day in ("1969-12-31", days_from_today(2), "2999-01-01", "not-a-date", "", None, "2024-02-30"):
            res = await add_expense(c, h, p["id"], "1", "USD", bad_day)
            assert res.status_code == 422, (bad_day, res.text)


async def test_text_lengths_are_bounded(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        ok = await add_expense(c, h, p["id"], "1", "USD", FRI, category="к" * 60, note="н" * 2000)
        assert ok.status_code == 201, ok.text
        too_long_category = await add_expense(c, h, p["id"], "1", "USD", FRI, category="к" * 61)
        too_long_note = await add_expense(c, h, p["id"], "1", "USD", FRI, note="н" * 2001)
        assert too_long_category.status_code == 422 and too_long_note.status_code == 422


async def test_category_is_trimmed_lowercased_and_blank_becomes_null(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        a = await add_ok(c, h, p["id"], "1", "USD", FRI, category="  Топливо ")
        b = await add_ok(c, h, p["id"], "1", "USD", FRI, category="ТОПЛИВО")
        blank = await add_ok(c, h, p["id"], "1", "USD", FRI, category="   ")
        spaced = await add_ok(c, h, p["id"], "1", "USD", FRI, category="мойка   и   полировка")
        assert a["category"] == b["category"] == "топливо"
        assert blank["category"] is None
        assert spaced["category"] == "мойка и полировка"


async def test_note_and_special_characters_roundtrip(db_ready, fake_fx):
    note = "Заправка 'Белнефть' 🚗; DROP TABLE fin_expenses;-- <script>1</script>"
    async with await client() as c:
        h, p = await _setup(c, "USD")
        e = await add_ok(c, h, p["id"], "1", "USD", FRI, note=note)
        assert e["note"] == note


async def test_unknown_body_fields_are_rejected(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        res = await add_expense(c, h, p["id"], "1", "USD", FRI, base_amount="999", owner="someone")
        assert res.status_code == 422, res.text  # a typo must not be silently dropped


# --- idempotency ---------------------------------------------------------------------


async def test_repeating_a_client_id_returns_the_same_expense_once(db_ready, fake_fx):
    cid = str(uuid.uuid4())
    async with await client() as c:
        h, p = await _setup(c, "USD")

        first = await add_expense(c, h, p["id"], "10", "USD", FRI, client_id=cid, note="первая")
        again = await add_expense(c, h, p["id"], "999", "USD", MON, client_id=cid, note="повтор")

        assert first.status_code == 201 and again.status_code == 200
        assert again.json()["id"] == first.json()["id"]
        assert again.json()["amount"] == "10.00" and again.json()["note"] == "первая"  # not overwritten
        assert again.json()["client_id"] == cid
    assert len(await _expense_rows()) == 1


async def test_concurrent_posts_with_one_client_id_do_not_duplicate(db_ready, fake_fx):
    cid = str(uuid.uuid4())
    async with await client() as c:
        h, p = await _setup(c, "USD")

        results = await asyncio.gather(
            *[add_expense(c, h, p["id"], "10", "USD", FRI, client_id=cid) for _ in range(4)]
        )

        assert all(r.status_code in (200, 201) for r in results), [r.text for r in results]
        assert len({r.json()["id"] for r in results}) == 1
    assert len(await _expense_rows()) == 1


async def test_client_id_of_another_project_is_a_conflict(db_ready, fake_fx):
    cid = str(uuid.uuid4())
    async with await client() as c:
        h = await register(c)
        first = await make_project(c, h, "A")
        second = await make_project(c, h, "B")
        await add_ok(c, h, first["id"], "10", "USD", FRI, client_id=cid)

        res = await add_expense(c, h, second["id"], "10", "USD", FRI, client_id=cid)

        assert res.status_code == 409, res.text
    assert len(await _expense_rows()) == 1


async def test_client_ids_are_independent_between_users(db_ready, fake_fx):
    cid = str(uuid.uuid4())
    async with await client() as c:
        alice, bob = await register(c), await register(c)
        pa = await make_project(c, alice, "A")
        pb = await make_project(c, bob, "B")
        one = await add_expense(c, alice, pa["id"], "10", "USD", FRI, client_id=cid)
        two = await add_expense(c, bob, pb["id"], "10", "USD", FRI, client_id=cid)
        assert one.status_code == 201 and two.status_code == 201
        assert one.json()["id"] != two.json()["id"]


async def test_malformed_client_id_is_rejected(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        res = await add_expense(c, h, p["id"], "10", "USD", FRI, client_id="not-a-uuid")
        assert res.status_code == 422


# --- listing --------------------------------------------------------------------------


async def _seed_list(c, h, pid):
    await add_ok(c, h, pid, "10", "USD", "2024-03-01", category="топливо")
    await add_ok(c, h, pid, "20", "USD", "2024-03-15", category="ремонт")
    await add_ok(c, h, pid, "30", "USD", "2024-04-01", category="топливо")
    await add_ok(c, h, pid, "40", "EUR", "2024-04-10", category="топливо", fx_rate="1.1")
    await add_ok(c, h, pid, "50", "USD", "2024-04-10")


async def test_list_orders_newest_first_with_a_total(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        await _seed_list(c, h, p["id"])

        res = await c.get(f"{FIN}/projects/{p['id']}/expenses", headers=h)

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total"] == 5 and body["limit"] == 50 and body["offset"] == 0
        dates = [e["spent_on"] for e in body["items"]]
        assert dates == sorted(dates, reverse=True)


async def test_list_filters_combine(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        await _seed_list(c, h, p["id"])
        url = f"{FIN}/projects/{p['id']}/expenses"

        def amounts(res):
            return sorted(e["amount"] for e in res.json()["items"])

        assert amounts(await c.get(f"{url}?category=топливо", headers=h)) == ["10.00", "30.00", "40.00"]
        assert amounts(await c.get(f"{url}?currency=eur", headers=h)) == ["40.00"]
        assert amounts(await c.get(f"{url}?from=2024-03-15&to=2024-04-01", headers=h)) == ["20.00", "30.00"]
        assert amounts(await c.get(f"{url}?from=2024-04-10", headers=h)) == ["40.00", "50.00"]
        assert amounts(await c.get(f"{url}?to=2024-03-01", headers=h)) == ["10.00"]
        both = await c.get(f"{url}?category=топливо&from=2024-04-01", headers=h)
        assert amounts(both) == ["30.00", "40.00"] and both.json()["total"] == 2
        assert (await c.get(f"{url}?category=nothing", headers=h)).json() == {
            "items": [], "total": 0, "limit": 50, "offset": 0,
        }


async def test_list_paginates(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        await _seed_list(c, h, p["id"])
        url = f"{FIN}/projects/{p['id']}/expenses"

        first = (await c.get(f"{url}?limit=2&offset=0", headers=h)).json()
        second = (await c.get(f"{url}?limit=2&offset=2", headers=h)).json()
        last = (await c.get(f"{url}?limit=2&offset=4", headers=h)).json()
        beyond = (await c.get(f"{url}?limit=2&offset=10", headers=h)).json()

        ids = [e["id"] for page in (first, second, last) for e in page["items"]]
        assert len(ids) == 5 and len(set(ids)) == 5
        assert first["total"] == second["total"] == last["total"] == 5
        assert beyond["items"] == [] and beyond["total"] == 5


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=201", "limit=-1", "offset=-1", "limit=abc", "from=bad", "to=2024-13-01",
     "from=2024-05-01&to=2024-04-01", "currency=XYZ", "currency=1"],
)
async def test_list_rejects_bad_query(db_ready, fake_fx, query):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        res = await c.get(f"{FIN}/projects/{p['id']}/expenses?{query}", headers=h)
        assert res.status_code == 422, (query, res.text)


async def test_list_accepts_the_maximum_page_size(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        res = await c.get(f"{FIN}/projects/{p['id']}/expenses?limit=200", headers=h)
        assert res.status_code == 200 and res.json()["limit"] == 200


async def test_list_handles_many_expenses(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        async with SessionLocal() as db:
            project = await db.get(FinProject, uuid.UUID(p["id"]))
            db.add_all(
                FinExpense(
                    user_id=project.user_id,
                    project_id=project.id,
                    amount=Decimal("1"),
                    currency="USD",
                    spent_on=date(2020, 1, 1) + timedelta(days=i % 1500),
                    base_amount=Decimal("1"),
                )
                for i in range(10_000)
            )
            await db.commit()

        listed = (await c.get(f"{FIN}/projects/{p['id']}/expenses?limit=200&offset=9900", headers=h)).json()
        assert listed["total"] == 10_000 and len(listed["items"]) == 100
        summary = (await c.get(f"{FIN}/projects/{p['id']}", headers=h)).json()
        assert summary["expenses_count"] == 10_000 and summary["total_base"] == "10000.00"


# --- editing ---------------------------------------------------------------------------


async def test_editing_only_text_does_not_touch_pricing(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    async with await client() as c:
        h, p = await _setup(c, "USD")
        e = await add_ok(c, h, p["id"], "100", "EUR", FRI)
        calls = fake_fx.call_count

        res = await c.patch(f"{FIN}/expenses/{e['id']}", json={"note": "новая", "category": "Налоги"}, headers=h)

        assert res.status_code == 200, res.text
        out = res.json()
        assert (out["note"], out["category"]) == ("новая", "налоги")
        assert (out["base_amount"], out["fx_rate"], out["fx_date"]) == ("110.00", "1.1", FRI)
        assert fake_fx.call_count == calls


async def test_editing_the_amount_reuses_the_stored_rate(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    async with await client() as c:
        h, p = await _setup(c, "USD")
        e = await add_ok(c, h, p["id"], "100", "EUR", FRI)
        calls = fake_fx.call_count

        out = (await c.patch(f"{FIN}/expenses/{e['id']}", json={"amount": "200"}, headers=h)).json()

        assert (out["amount"], out["base_amount"], out["fx_rate"]) == ("200.00", "220.00", "1.1")
        assert fake_fx.call_count == calls


async def test_editing_the_date_fetches_the_rate_of_the_new_date(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    fake_fx.set("EUR", MON, "1.20")
    async with await client() as c:
        h, p = await _setup(c, "USD")
        e = await add_ok(c, h, p["id"], "100", "EUR", FRI)

        out = (await c.patch(f"{FIN}/expenses/{e['id']}", json={"spent_on": MON}, headers=h)).json()

        assert (out["spent_on"], out["base_amount"], out["fx_date"]) == (MON, "120.00", MON)


async def test_editing_the_currency_reprices(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    fake_fx.set("GBP", FRI, "1.25")
    async with await client() as c:
        h, p = await _setup(c, "USD")
        e = await add_ok(c, h, p["id"], "100", "EUR", FRI)

        out = (await c.patch(f"{FIN}/expenses/{e['id']}", json={"currency": "gbp"}, headers=h)).json()
        assert (out["currency"], out["base_amount"], out["fx_rate"]) == ("GBP", "125.00", "1.25")

        into_base = (await c.patch(f"{FIN}/expenses/{e['id']}", json={"currency": "USD"}, headers=h)).json()
        assert (into_base["base_amount"], into_base["fx_rate"], into_base["fx_source"]) == ("100.00", "1", None)


async def test_manual_rate_can_be_set_then_dropped_by_an_explicit_null(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    async with await client() as c:
        h, p = await _setup(c, "USD")
        e = await add_ok(c, h, p["id"], "100", "EUR", FRI)

        manual = (await c.patch(f"{FIN}/expenses/{e['id']}", json={"fx_rate": "1.3"}, headers=h)).json()
        assert (manual["base_amount"], manual["fx_source"], manual["fx_date"]) == ("130.00", "manual", None)

        # Changing only the amount keeps the owner's rate.
        bigger = (await c.patch(f"{FIN}/expenses/{e['id']}", json={"amount": "200"}, headers=h)).json()
        assert (bigger["base_amount"], bigger["fx_source"]) == ("260.00", "manual")

        auto = (await c.patch(f"{FIN}/expenses/{e['id']}", json={"fx_rate": None}, headers=h)).json()
        assert (auto["base_amount"], auto["fx_source"], auto["fx_rate"]) == ("220.00", "ecb", "1.1")


async def test_editing_a_currency_drops_a_manual_rate_that_no_longer_applies(db_ready, fake_fx):
    fake_fx.set("GBP", FRI, "1.25")
    async with await client() as c:
        h, p = await _setup(c, "USD")
        e = await add_ok(c, h, p["id"], "100", "EUR", FRI, fx_rate="1.3")

        out = (await c.patch(f"{FIN}/expenses/{e['id']}", json={"currency": "GBP"}, headers=h)).json()

        assert (out["fx_source"], out["base_amount"]) == ("ecb", "125.00")


async def test_editing_an_unpriced_expense_prices_it_once_a_rate_exists(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        e = await add_ok(c, h, p["id"], "100", "EUR", FRI)
        assert e["base_amount"] is None
        fake_fx.set("EUR", FRI, "1.10")

        out = (await c.patch(f"{FIN}/expenses/{e['id']}", json={"amount": "50"}, headers=h)).json()

        assert out["base_amount"] == "55.00" and out["fx_source"] == "ecb"


async def test_editing_clears_optional_text_with_null(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        e = await add_ok(c, h, p["id"], "1", "USD", FRI, category="топливо", note="x")
        out = (await c.patch(f"{FIN}/expenses/{e['id']}", json={"category": None, "note": None}, headers=h)).json()
        assert out["category"] is None and out["note"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"amount": "0"},
        {"amount": None},
        {"amount": "1000000000000.01"},
        {"currency": "XYZ"},
        {"currency": None},
        {"spent_on": days_from_today(2)},
        {"spent_on": "1969-12-31"},
        {"spent_on": None},
        {"category": "к" * 61},
        {"note": "н" * 2001},
        {"fx_rate": "0"},
        {"project_id": str(uuid.uuid4())},   # an expense cannot be moved between projects
        {"client_id": str(uuid.uuid4())},    # nor re-keyed
        {"base_amount": "1"},
    ],
)
async def test_editing_rejects_bad_input(db_ready, fake_fx, body):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        e = await add_ok(c, h, p["id"], "10", "USD", FRI)
        res = await c.patch(f"{FIN}/expenses/{e['id']}", json=body, headers=h)
        assert res.status_code == 422, (body, res.text)


async def test_foreign_expense_cannot_be_edited_or_deleted(db_ready, fake_fx):
    async with await client() as c:
        alice, bob = await register(c), await register(c)
        p = await make_project(c, alice, "A")
        e = await add_ok(c, alice, p["id"], "10", "USD", FRI)

        assert (await c.patch(f"{FIN}/expenses/{e['id']}", json={"note": "x"}, headers=bob)).status_code == 404
        assert (await c.delete(f"{FIN}/expenses/{e['id']}", headers=bob)).status_code == 404
        assert (await c.patch(f"{FIN}/expenses/{uuid.uuid4()}", json={"note": "x"}, headers=alice)).status_code == 404
    assert len(await _expense_rows()) == 1


async def test_delete_expense(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        e = await add_ok(c, h, p["id"], "10", "USD", FRI)

        assert (await c.delete(f"{FIN}/expenses/{e['id']}", headers=h)).status_code == 204
        assert (await c.delete(f"{FIN}/expenses/{e['id']}", headers=h)).status_code == 404
        assert (await c.get(f"{FIN}/projects/{p['id']}", headers=h)).json()["expenses_count"] == 0
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(FinExpense)) == 0
