"""Finance API: report, revalue, FX preview, category hints, logging hygiene."""
from __future__ import annotations

import logging
import time

import pytest
from sqlalchemy import func, select

from core.db import SessionLocal
from models.finance import FxRateDaily
from services import fx_history
from tests.finance_client import (
    API,
    FIN,
    add_ok,
    client,
    days_from_today,
    make_project,
    register,
    today_utc,
)

FRI = "2024-03-29"
MON = "2024-04-01"


async def _setup(c, base: str = "USD", name: str = "Машина"):
    h = await register(c)
    return h, await make_project(c, h, name, base)


# --- report ------------------------------------------------------------------------


async def _fill_report_project(c, h, pid, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    fake_fx.set("GEL", "2024-04-09", "0.5")
    await add_ok(c, h, pid, "100", "EUR", FRI, category="Топливо")
    await add_ok(c, h, pid, "40", "USD", "2024-03-31", category="топливо")
    await add_ok(c, h, pid, "25.5", "USD", "2024-04-02", category="ремонт")
    await add_ok(c, h, pid, "-10", "USD", "2024-04-05", category="ремонт")          # refund
    await add_ok(c, h, pid, "5000", "KZT", "2024-04-06", category="топливо")         # no rate
    await add_ok(c, h, pid, "7", "USD", "2024-04-07")
    await add_ok(c, h, pid, "100", "KZT", "2024-04-08")                              # no rate
    await add_ok(c, h, pid, "50", "GEL", "2024-04-09", category="ремонт")


async def test_report_aggregates_in_the_base_currency(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        await _fill_report_project(c, h, p["id"], fake_fx)

        res = await c.get(f"{FIN}/projects/{p['id']}/report", headers=h)

        assert res.status_code == 200, res.text
        r = res.json()
        assert r["project"] == {
            "id": p["id"], "name": "Машина", "base_currency": "USD", "status": "active", "started_on": None,
        }
        assert r["period"] == {"from": None, "to": None}
        assert r["base_currency"] == "USD"
        assert r["total_base"] == "197.50"        # 110 + 40 + 25.5 - 10 + 7 + 25
        assert r["expenses_count"] == 8
        assert r["unpriced_count"] == 2
        assert r["unpriced_by_currency"] == [{"currency": "KZT", "count": 2, "amount": "5100.00"}]
        assert r["by_category"] == [
            {"category": "топливо", "total_base": "150.00", "count": 3, "unpriced_count": 1},
            {"category": "ремонт", "total_base": "40.50", "count": 3, "unpriced_count": 0},
            {"category": None, "total_base": "7.00", "count": 2, "unpriced_count": 1},
        ]
        assert r["by_month"] == [
            {"month": "2024-03", "total_base": "150.00", "count": 2, "unpriced_count": 0},
            {"month": "2024-04", "total_base": "47.50", "count": 6, "unpriced_count": 2},
        ]
        assert r["by_currency"] == [
            {"currency": "EUR", "amount": "100.00", "total_base": "110.00", "count": 1, "unpriced_count": 0},
            {"currency": "USD", "amount": "62.50", "total_base": "62.50", "count": 4, "unpriced_count": 0},
            {"currency": "GEL", "amount": "50.00", "total_base": "25.00", "count": 1, "unpriced_count": 0},
            {"currency": "KZT", "amount": "5100.00", "total_base": "0.00", "count": 2, "unpriced_count": 2},
        ]


async def test_report_period_filters_every_figure(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        await _fill_report_project(c, h, p["id"], fake_fx)

        r = (await c.get(f"{FIN}/projects/{p['id']}/report?from=2024-04-01&to=2024-04-30", headers=h)).json()

        assert r["period"] == {"from": "2024-04-01", "to": "2024-04-30"}
        assert r["total_base"] == "47.50" and r["expenses_count"] == 6 and r["unpriced_count"] == 2
        assert [m["month"] for m in r["by_month"]] == ["2024-04"]
        assert {x["currency"] for x in r["by_currency"]} == {"USD", "KZT", "GEL"}

        edge = (await c.get(f"{FIN}/projects/{p['id']}/report?from=2024-03-31&to=2024-03-31", headers=h)).json()
        assert edge["expenses_count"] == 1 and edge["total_base"] == "40.00"  # both ends inclusive

        nothing = (await c.get(f"{FIN}/projects/{p['id']}/report?from=2030-01-01", headers=h)).json()
        assert nothing["expenses_count"] == 0 and nothing["by_month"] == []


async def test_empty_project_report_is_zeroed_not_broken(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "EUR")
        r = (await c.get(f"{FIN}/projects/{p['id']}/report", headers=h)).json()
        assert r["base_currency"] == "EUR"
        assert r["total_base"] == "0.00" and r["expenses_count"] == 0 and r["unpriced_count"] == 0
        assert r["unpriced_by_currency"] == r["by_category"] == r["by_month"] == r["by_currency"] == []


async def test_report_is_decimal_exact(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        for _ in range(10):
            await add_ok(c, h, p["id"], "0.10", "USD", FRI)  # float sums would drift
        r = (await c.get(f"{FIN}/projects/{p['id']}/report", headers=h)).json()
        assert r["total_base"] == "1.00"


@pytest.mark.parametrize("query", ["from=bad", "to=2024-02-30", "from=2024-05-01&to=2024-04-01"])
async def test_report_rejects_bad_period(db_ready, fake_fx, query):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        res = await c.get(f"{FIN}/projects/{p['id']}/report?{query}", headers=h)
        assert res.status_code == 422, (query, res.text)


async def test_report_only_counts_its_own_project(db_ready, fake_fx):
    async with await client() as c:
        h = await register(c)
        a = await make_project(c, h, "A")
        b = await make_project(c, h, "B")
        await add_ok(c, h, a["id"], "10", "USD", FRI, category="топливо")
        await add_ok(c, h, b["id"], "99", "USD", FRI, category="другое")
        r = (await c.get(f"{FIN}/projects/{a['id']}/report", headers=h)).json()
        assert r["total_base"] == "10.00" and [x["category"] for x in r["by_category"]] == ["топливо"]


# --- revalue -----------------------------------------------------------------------


async def test_revalue_prices_the_expenses_that_had_no_rate(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        await add_ok(c, h, p["id"], "100", "EUR", FRI)       # no rate yet
        await add_ok(c, h, p["id"], "100", "EUR", MON)       # still no rate after revalue
        await add_ok(c, h, p["id"], "5000", "KZT", FRI)      # never gets one
        await add_ok(c, h, p["id"], "10", "USD", FRI)        # priced
        fake_fx.set("EUR", FRI, "1.10")

        res = await c.post(f"{FIN}/projects/{p['id']}/revalue", headers=h)

        assert res.status_code == 200, res.text
        assert res.json() == {"revalued": 1, "unpriced_count": 2}
        rows = (await c.get(f"{FIN}/projects/{p['id']}/expenses", headers=h)).json()["items"]
        priced = {(r["currency"], r["spent_on"]): r for r in rows}
        assert priced[("EUR", FRI)]["base_amount"] == "110.00"
        assert priced[("EUR", FRI)]["fx_source"] == "ecb" and priced[("EUR", FRI)]["fx_date"] == FRI
        assert priced[("EUR", MON)]["base_amount"] is None
        assert priced[("KZT", FRI)]["base_amount"] is None


async def test_revalue_leaves_priced_and_manual_expenses_alone(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    async with await client() as c:
        h, p = await _setup(c, "USD")
        auto = await add_ok(c, h, p["id"], "100", "EUR", FRI)
        manual = await add_ok(c, h, p["id"], "100", "EUR", FRI, fx_rate="1.5")
        fake_fx.set("EUR", FRI, "9.99")  # the market "changes": priced rows must not follow it
        fake_fx.calls.clear()

        res = await c.post(f"{FIN}/projects/{p['id']}/revalue", headers=h)

        assert res.json() == {"revalued": 0, "unpriced_count": 0}
        assert fake_fx.call_count == 0
        rows = {r["id"]: r for r in (await c.get(f"{FIN}/projects/{p['id']}/expenses", headers=h)).json()["items"]}
        assert rows[auto["id"]]["base_amount"] == "110.00"
        assert rows[manual["id"]]["base_amount"] == "150.00"


async def test_revalue_asks_each_currency_and_date_once(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        for _ in range(3):
            await add_ok(c, h, p["id"], "10", "EUR", FRI)
        fake_fx.set("EUR", FRI, "1.10")
        fake_fx.calls.clear()

        res = await c.post(f"{FIN}/projects/{p['id']}/revalue", headers=h)

        assert res.json() == {"revalued": 3, "unpriced_count": 0}
        assert fake_fx.calls.count("ecb:EUR:2024-03-29") == 1


async def test_revalue_survives_a_provider_outage(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        await add_ok(c, h, p["id"], "100", "EUR", FRI)
        fake_fx.fail_all()

        res = await c.post(f"{FIN}/projects/{p['id']}/revalue", headers=h)

        assert res.status_code == 200 and res.json() == {"revalued": 0, "unpriced_count": 1}


async def test_revalue_shares_one_time_budget_across_expenses(db_ready, fake_fx, monkeypatch):
    monkeypatch.setattr(fx_history, "FX_BUDGET_SECONDS", 0.3)
    fake_fx.hang()
    async with await client() as c:
        h, p = await _setup(c, "USD")
        for day in ("2024-03-25", "2024-03-26", "2024-03-27", "2024-03-28"):
            await add_ok(c, h, p["id"], "10", "EUR", day)
        started = time.monotonic()

        res = await c.post(f"{FIN}/projects/{p['id']}/revalue", headers=h)

        assert res.status_code == 200 and res.json() == {"revalued": 0, "unpriced_count": 4}
        assert time.monotonic() - started < 0.9  # one budget, not four


async def test_revalue_stays_inside_its_project(db_ready, fake_fx):
    async with await client() as c:
        h = await register(c)
        a = await make_project(c, h, "A")
        b = await make_project(c, h, "B")
        await add_ok(c, h, b["id"], "100", "EUR", FRI)
        fake_fx.set("EUR", FRI, "1.10")

        res = await c.post(f"{FIN}/projects/{a['id']}/revalue", headers=h)

        assert res.json() == {"revalued": 0, "unpriced_count": 0}
        other = (await c.get(f"{FIN}/projects/{b['id']}", headers=h)).json()
        assert other["unpriced_count"] == 1


async def test_revalue_is_rate_limited(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        codes = [
            (await c.post(f"{FIN}/projects/{p['id']}/revalue", headers=h)).status_code for _ in range(12)
        ]
        assert codes[:10] == [200] * 10
        assert codes[-1] == 429


# --- FX preview ----------------------------------------------------------------------


async def test_fx_preview_returns_the_rate_for_the_form(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    async with await client() as c:
        h = await register(c)
        res = await c.get(f"{FIN}/fx?currency=EUR&date={FRI}&base=USD", headers=h)
        assert res.status_code == 200, res.text
        assert res.json() == {
            "currency": "EUR", "base": "USD", "date": FRI, "rate": "1.1", "fx_date": FRI, "source": "ecb",
        }


async def test_fx_preview_defaults_to_the_users_base_currency_and_today(db_ready, fake_fx):
    today = today_utc().isoformat()
    fake_fx.set("EUR", today, "1.10")
    fake_fx.set("BYN", today, "0.25")
    async with await client() as c:
        h = await register(c)
        await c.put(f"{API}/account/base-currency", json={"base_currency": "BYN"}, headers=h)

        res = await c.get(f"{FIN}/fx?currency=eur", headers=h)

        body = res.json()
        assert (body["base"], body["date"], body["rate"], body["source"]) == ("BYN", today, "4.4", "ecb")


async def test_fx_preview_without_a_rate_answers_nulls_not_an_error(db_ready, fake_fx):
    async with await client() as c:
        h = await register(c)
        res = await c.get(f"{FIN}/fx?currency=KZT&date={FRI}&base=USD", headers=h)
        assert res.status_code == 200
        assert res.json() == {
            "currency": "KZT", "base": "USD", "date": FRI, "rate": None, "fx_date": None, "source": None,
        }


async def test_fx_preview_same_currency_is_one(db_ready, fake_fx):
    async with await client() as c:
        h = await register(c)
        body = (await c.get(f"{FIN}/fx?currency=USD&date={FRI}&base=USD", headers=h)).json()
        assert body["rate"] == "1" and body["source"] is None
        assert fake_fx.call_count == 0


async def test_fx_preview_on_a_weekend_reports_the_real_date(db_ready, fake_fx):
    fake_fx.ecb("EUR", "2024-03-30", "1.10", actual=FRI)
    async with await client() as c:
        h = await register(c)
        body = (await c.get(f"{FIN}/fx?currency=EUR&date=2024-03-30&base=USD", headers=h)).json()
        assert (body["date"], body["fx_date"]) == ("2024-03-30", FRI)


async def test_fx_preview_saves_the_fetched_rate(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    async with await client() as c:
        h = await register(c)
        await c.get(f"{FIN}/fx?currency=EUR&date={FRI}&base=USD", headers=h)
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(FxRateDaily)) == 1


@pytest.mark.parametrize(
    "query",
    [
        "",                                       # currency is required
        "currency=XYZ",
        "currency=EU",
        "currency=EUR&base=XYZ",
        "currency=EUR&date=bad",
        f"currency=EUR&date={days_from_today(2)}",
        "currency=EUR&date=1969-12-31",
    ],
)
async def test_fx_preview_rejects_bad_query(db_ready, fake_fx, query):
    async with await client() as c:
        h = await register(c)
        res = await c.get(f"{FIN}/fx?{query}", headers=h)
        assert res.status_code == 422, (query, res.text)


# --- categories ------------------------------------------------------------------------

STANDARD = ["топливо", "ремонт", "страховка", "налоги", "парковка", "мойка", "запчасти", "прочее"]


async def test_categories_for_a_new_user_are_the_standard_list(db_ready):
    async with await client() as c:
        h = await register(c)
        res = await c.get(f"{FIN}/categories", headers=h)
        assert res.status_code == 200
        assert res.json() == [{"name": n, "count": 0} for n in STANDARD]


async def test_categories_put_the_users_own_by_frequency_first(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c, "USD")
        for _ in range(3):
            await add_ok(c, h, p["id"], "1", "USD", FRI, category="обслуживание")
        await add_ok(c, h, p["id"], "1", "USD", FRI, category="топливо")
        for _ in range(2):
            await add_ok(c, h, p["id"], "1", "USD", FRI, category="шины")
        await add_ok(c, h, p["id"], "1", "USD", FRI, category="авария")
        await add_ok(c, h, p["id"], "1", "USD", FRI)  # uncategorised: never suggested

        names = (await c.get(f"{FIN}/categories", headers=h)).json()

        assert names[:4] == [
            {"name": "обслуживание", "count": 3},
            {"name": "шины", "count": 2},
            {"name": "авария", "count": 1},      # ties: alphabetical
            {"name": "топливо", "count": 1},
        ]
        assert [n["name"] for n in names[4:]] == [s for s in STANDARD if s != "топливо"]
        assert all(n["count"] == 0 for n in names[4:])
        assert len({n["name"] for n in names}) == len(names)  # no duplicates


async def test_categories_span_projects_but_not_users(db_ready, fake_fx):
    async with await client() as c:
        alice, bob = await register(c), await register(c)
        pa1 = await make_project(c, alice, "A1")
        pa2 = await make_project(c, alice, "A2")
        pb = await make_project(c, bob, "B")
        await add_ok(c, alice, pa1["id"], "1", "USD", FRI, category="шины")
        await add_ok(c, alice, pa2["id"], "1", "USD", FRI, category="шины")
        await add_ok(c, bob, pb["id"], "1", "USD", FRI, category="секрет")

        mine = {n["name"]: n["count"] for n in (await c.get(f"{FIN}/categories", headers=alice)).json()}
        theirs = {n["name"]: n["count"] for n in (await c.get(f"{FIN}/categories", headers=bob)).json()}

        assert mine["шины"] == 2 and "секрет" not in mine
        assert theirs["секрет"] == 1 and "шины" not in theirs


# --- logs ------------------------------------------------------------------------------


async def test_logs_never_carry_amounts_notes_or_project_names(db_ready, fake_fx, caplog):
    caplog.set_level(logging.DEBUG, logger="kapital")
    secrets = ["Проект-Секрет-7731", "ЗАМЕТКА-ВЛАДЕЛЬЦА-5521", "4242.42"]
    fake_fx.fail_all()  # forces warnings on the FX path
    async with await client() as c:
        h = await register(c)
        p = await make_project(c, h, secrets[0], "USD", note=secrets[1])
        await add_ok(c, h, p["id"], secrets[2], "EUR", FRI, note=secrets[1])
        await c.patch(f"{FIN}/projects/{p['id']}", json={"base_currency": "GEL"}, headers=h)
        await c.post(f"{FIN}/projects/{p['id']}/revalue", headers=h)
        await c.get(f"{FIN}/fx?currency=EUR&date={FRI}&base=USD", headers=h)

    ours = [r for r in caplog.records if r.name.startswith("kapital")]
    assert any(r.name == "kapital.fx_history" for r in ours), "expected the FX path to log its failure"
    text = "\n".join(r.getMessage() for r in ours)
    for secret in secrets:
        assert secret not in text, secret
    assert "42.42" not in text
