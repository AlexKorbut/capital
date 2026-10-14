"""Finance projects API: projects, validation, isolation, cascade delete, base change."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from core.db import SessionLocal
from models.finance import FinExpense, FinProject
from tests.finance_client import (
    API,
    FIN,
    add_ok,
    client,
    make_project,
    register,
)

MARCH_29 = "2024-03-29"  # Friday


# --- auth -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/projects"),
        ("POST", "/projects"),
        ("GET", f"/projects/{uuid.uuid4()}"),
        ("PATCH", f"/projects/{uuid.uuid4()}"),
        ("DELETE", f"/projects/{uuid.uuid4()}"),
        ("GET", f"/projects/{uuid.uuid4()}/expenses"),
        ("POST", f"/projects/{uuid.uuid4()}/expenses"),
        ("PATCH", f"/expenses/{uuid.uuid4()}"),
        ("DELETE", f"/expenses/{uuid.uuid4()}"),
        ("GET", f"/projects/{uuid.uuid4()}/report"),
        ("POST", f"/projects/{uuid.uuid4()}/revalue"),
        ("GET", "/fx?currency=EUR"),
        ("GET", "/categories"),
    ],
)
async def test_every_finance_endpoint_requires_authentication(db_ready, method, path):
    async with await client() as c:
        res = await c.request(method, f"{FIN}{path}")
        assert res.status_code == 401, (method, path, res.text)


async def test_personal_api_token_works_like_a_session(db_ready):
    async with await client() as c:
        h = await register(c)
        minted = await c.post(f"{API}/account/tokens", json={"name": "jarvis"}, headers=h)
        token_headers = {"Authorization": f"Bearer {minted.json()['token']}"}

        created = await c.post(
            f"{FIN}/projects", json={"name": "Машина", "base_currency": "USD"}, headers=token_headers
        )
        assert created.status_code == 201, created.text

        listed = await c.get(f"{FIN}/projects", headers=h)  # same user via the JWT
        assert [p["name"] for p in listed.json()] == ["Машина"]


# --- create -----------------------------------------------------------------------


async def test_create_project_minimal_uses_the_users_base_currency(db_ready):
    async with await client() as c:
        h = await register(c)
        await c.put(f"{API}/account/base-currency", json={"base_currency": "byn"}, headers=h)

        res = await c.post(f"{FIN}/projects", json={"name": "  Машина  "}, headers=h)

        assert res.status_code == 201, res.text
        p = res.json()
        assert p["name"] == "Машина"  # trimmed
        assert p["base_currency"] == "BYN"
        assert p["status"] == "active"
        assert p["note"] is None and p["started_on"] is None
        assert uuid.UUID(p["id"])
        assert p["created_at"] and p["updated_at"]
        # Brand-new project: totals are zero strings, not numbers or nulls.
        assert p["total_base"] == "0.00"
        assert p["expenses_count"] == 0 and p["unpriced_count"] == 0
        assert p["last_spent_on"] is None


async def test_create_project_with_everything_normalises_the_currency(db_ready):
    async with await client() as c:
        h = await register(c)
        res = await c.post(
            f"{FIN}/projects",
            json={"name": "Квартира", "base_currency": "eur", "started_on": "2026-01-15", "note": "ремонт кухни"},
            headers=h,
        )
        assert res.status_code == 201, res.text
        p = res.json()
        assert (p["base_currency"], p["started_on"], p["note"]) == ("EUR", "2026-01-15", "ремонт кухни")


async def test_user_base_currency_outside_iso_falls_back_to_usd(db_ready):
    async with await client() as c:
        h = await register(c)
        await c.put(f"{API}/account/base-currency", json={"base_currency": "zz"}, headers=h)
        res = await c.post(f"{FIN}/projects", json={"name": "X"}, headers=h)
        assert res.json()["base_currency"] == "USD"


@pytest.mark.parametrize(
    "body",
    [
        {},                                             # name is required
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 121},
        {"name": "ok", "base_currency": "XYZ"},         # not an ISO-4217 code
        {"name": "ok", "base_currency": "EU"},
        {"name": "ok", "base_currency": "EURO"},
        {"name": "ok", "base_currency": "U$D"},
        {"name": "ok", "base_currency": "ЕВР"},         # cyrillic look-alikes
        {"name": "ok", "base_currency": 123},
        {"name": "ok", "note": "n" * 2001},
        {"name": "ok", "started_on": "not-a-date"},
        {"name": "ok", "status": "archived"},           # status is not settable on create
    ],
)
async def test_create_project_rejects_bad_input(db_ready, body):
    async with await client() as c:
        h = await register(c)
        res = await c.post(f"{FIN}/projects", json=body, headers=h)
        assert res.status_code == 422, (body, res.text)


async def test_project_name_exactly_at_the_limit_is_accepted(db_ready):
    async with await client() as c:
        h = await register(c)
        res = await c.post(f"{FIN}/projects", json={"name": "я" * 120, "base_currency": "USD"}, headers=h)
        assert res.status_code == 201


async def test_duplicate_project_name_is_a_conflict_but_other_users_may_reuse_it(db_ready):
    async with await client() as c:
        alice, bob = await register(c), await register(c)
        await make_project(c, alice, "Машина")

        dup = await c.post(f"{FIN}/projects", json={"name": "Машина", "base_currency": "USD"}, headers=alice)
        assert dup.status_code == 409, dup.text

        other = await c.post(f"{FIN}/projects", json={"name": "Машина", "base_currency": "USD"}, headers=bob)
        assert other.status_code == 201


async def test_special_characters_survive_a_roundtrip(db_ready):
    name = "Дача 🏡 — O'Brien; DROP TABLE users;--"
    async with await client() as c:
        h = await register(c)
        p = await make_project(c, h, name, note="ёжик 😀 \"quotes\" <b>x</b>")
        got = await c.get(f"{FIN}/projects/{p['id']}", headers=h)
        assert got.json()["name"] == name
        assert got.json()["note"] == "ёжик 😀 \"quotes\" <b>x</b>"


# --- list --------------------------------------------------------------------------


async def test_list_projects_filters_by_status_and_carries_totals(db_ready, fake_fx):
    fake_fx.set("EUR", MARCH_29, "1.10")
    async with await client() as c:
        h = await register(c)
        car = await make_project(c, h, "Машина", "USD")
        old = await make_project(c, h, "Старый ремонт", "USD")
        await c.patch(f"{FIN}/projects/{old['id']}", json={"status": "archived"}, headers=h)
        await add_ok(c, h, car["id"], "100", "EUR", MARCH_29)       # 110.00 USD
        await add_ok(c, h, car["id"], "40.5", "USD", "2024-04-02")  # identity
        await add_ok(c, h, car["id"], "1000", "KZT", "2024-04-01")  # no rate

        everything = (await c.get(f"{FIN}/projects", headers=h)).json()
        assert [p["name"] for p in everything] == ["Машина", "Старый ремонт"]  # active first

        active = (await c.get(f"{FIN}/projects?status=active", headers=h)).json()
        assert [p["name"] for p in active] == ["Машина"]
        p = active[0]
        assert p["total_base"] == "150.50"
        assert p["expenses_count"] == 3
        assert p["unpriced_count"] == 1
        assert p["last_spent_on"] == "2024-04-02"

        archived = (await c.get(f"{FIN}/projects?status=archived", headers=h)).json()
        assert [p["name"] for p in archived] == ["Старый ремонт"]
        assert archived[0]["total_base"] == "0.00"

        bad = await c.get(f"{FIN}/projects?status=deleted", headers=h)
        assert bad.status_code == 422


async def test_list_is_scoped_to_the_caller(db_ready):
    async with await client() as c:
        alice, bob = await register(c), await register(c)
        await make_project(c, alice, "A1")
        await make_project(c, bob, "B1")
        assert [p["name"] for p in (await c.get(f"{FIN}/projects", headers=alice)).json()] == ["A1"]
        assert [p["name"] for p in (await c.get(f"{FIN}/projects", headers=bob)).json()] == ["B1"]


# --- get / patch / delete ---------------------------------------------------------


async def test_get_patch_project(db_ready):
    async with await client() as c:
        h = await register(c)
        p = await make_project(c, h, "Машина", note="старая", started_on="2026-01-01")

        got = await c.get(f"{FIN}/projects/{p['id']}", headers=h)
        assert got.status_code == 200 and got.json()["id"] == p["id"]

        patched = await c.patch(
            f"{FIN}/projects/{p['id']}",
            json={"name": "Toyota", "status": "archived", "note": "новая"},
            headers=h,
        )
        assert patched.status_code == 200, patched.text
        out = patched.json()
        assert (out["name"], out["status"], out["note"]) == ("Toyota", "archived", "новая")
        assert out["started_on"] == "2026-01-01"  # untouched fields stay

        # Explicit null clears optional fields; absent keys leave them alone.
        cleared = await c.patch(
            f"{FIN}/projects/{p['id']}", json={"note": None, "started_on": None}, headers=h
        )
        assert cleared.json()["note"] is None and cleared.json()["started_on"] is None
        assert cleared.json()["name"] == "Toyota"

        noop = await c.patch(f"{FIN}/projects/{p['id']}", json={}, headers=h)
        assert noop.status_code == 200 and noop.json()["name"] == "Toyota"


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "x" * 121},
        {"name": None},
        {"base_currency": "XYZ"},
        {"base_currency": None},
        {"status": "deleted"},
        {"status": None},
        {"note": "n" * 2001},
    ],
)
async def test_patch_project_rejects_bad_input(db_ready, body):
    async with await client() as c:
        h = await register(c)
        p = await make_project(c, h)
        res = await c.patch(f"{FIN}/projects/{p['id']}", json=body, headers=h)
        assert res.status_code == 422, (body, res.text)


async def test_patch_to_an_existing_name_is_a_conflict(db_ready):
    async with await client() as c:
        h = await register(c)
        await make_project(c, h, "A")
        b = await make_project(c, h, "B")
        res = await c.patch(f"{FIN}/projects/{b['id']}", json={"name": "A"}, headers=h)
        assert res.status_code == 409
        same = await c.patch(f"{FIN}/projects/{b['id']}", json={"name": "B"}, headers=h)
        assert same.status_code == 200  # renaming to itself is fine


async def test_foreign_and_unknown_projects_are_404(db_ready):
    async with await client() as c:
        alice, bob = await register(c), await register(c)
        p = await make_project(c, alice, "Секрет")
        pid = p["id"]
        for method, path, body in [
            ("GET", f"/projects/{pid}", None),
            ("PATCH", f"/projects/{pid}", {"name": "hack"}),
            ("DELETE", f"/projects/{pid}", None),
            ("GET", f"/projects/{pid}/expenses", None),
            ("POST", f"/projects/{pid}/expenses", {"amount": "1", "currency": "USD", "spent_on": MARCH_29}),
            ("GET", f"/projects/{pid}/report", None),
            ("POST", f"/projects/{pid}/revalue", None),
            ("GET", f"/projects/{uuid.uuid4()}", None),
        ]:
            res = await c.request(method, f"{FIN}{path}", json=body, headers=bob)
            assert res.status_code == 404, (method, path, res.text)
        # Alice's project is untouched.
        mine = await c.get(f"{FIN}/projects/{pid}", headers=alice)
        assert mine.json()["name"] == "Секрет"


async def test_malformed_ids_are_rejected_not_500(db_ready):
    async with await client() as c:
        h = await register(c)
        assert (await c.get(f"{FIN}/projects/not-a-uuid", headers=h)).status_code == 422
        assert (await c.delete(f"{FIN}/expenses/not-a-uuid", headers=h)).status_code == 422


async def test_delete_project_removes_its_expenses_explicitly(db_ready, fake_fx):
    async with await client() as c:
        h = await register(c)
        doomed = await make_project(c, h, "Doomed")
        kept = await make_project(c, h, "Kept")
        for _ in range(3):
            await add_ok(c, h, doomed["id"], "10", "USD", MARCH_29)
        await add_ok(c, h, kept["id"], "5", "USD", MARCH_29)

        res = await c.delete(f"{FIN}/projects/{doomed['id']}", headers=h)
        assert res.status_code == 204
        assert (await c.get(f"{FIN}/projects/{doomed['id']}", headers=h)).status_code == 404

    async with SessionLocal() as db:
        # SQLite does not enforce FKs here: orphans would show up as leftover rows.
        remaining = list(await db.scalars(select(FinExpense)))
        assert len(remaining) == 1 and str(remaining[0].project_id) == kept["id"]
        assert await db.scalar(select(func.count()).select_from(FinProject)) == 1


# --- base currency change ---------------------------------------------------------


async def test_changing_the_base_currency_reprices_every_expense(db_ready, fake_fx):
    for day in (MARCH_29, "2024-04-01"):
        fake_fx.set("EUR", day, "1.10")
        fake_fx.set("BYN", day, "0.25")
    async with await client() as c:
        h = await register(c)
        p = await make_project(c, h, "Машина", "USD")
        eur = await add_ok(c, h, p["id"], "100", "EUR", MARCH_29)       # 110.00 USD
        byn = await add_ok(c, h, p["id"], "400", "BYN", "2024-04-01")   # 100.00 USD
        usd = await add_ok(c, h, p["id"], "10", "USD", MARCH_29)
        assert (eur["base_amount"], byn["base_amount"], usd["base_amount"]) == ("110.00", "100.00", "10.00")

        res = await c.patch(f"{FIN}/projects/{p['id']}", json={"base_currency": "byn"}, headers=h)

        assert res.status_code == 200, res.text
        out = res.json()
        assert out["base_currency"] == "BYN"
        rows = (await c.get(f"{FIN}/projects/{p['id']}/expenses?limit=200", headers=h)).json()["items"]
        by_currency = {r["currency"]: r for r in rows}
        assert by_currency["EUR"]["base_amount"] == "440.00"        # 100 * 1.10 / 0.25
        assert by_currency["EUR"]["base_currency"] == "BYN"
        assert by_currency["BYN"]["base_amount"] == "400.00"        # identity now
        assert by_currency["BYN"]["fx_rate"] == "1" and by_currency["BYN"]["fx_source"] is None
        assert by_currency["USD"]["base_amount"] == "40.00"         # 10 / 0.25
        assert out["total_base"] == "880.00"
        assert out["unpriced_count"] == 0


async def test_base_change_keeps_a_manual_rate_by_converting_it(db_ready, fake_fx):
    fake_fx.set("BYN", MARCH_29, "0.25")  # 1 BYN = 0.25 USD
    async with await client() as c:
        h = await register(c)
        p = await make_project(c, h, "Поездка", "BYN")
        # I paid by card: 1 EUR = 3.60 BYN, whatever the market said.
        e = await add_ok(c, h, p["id"], "100", "EUR", MARCH_29, fx_rate="3.60")
        assert (e["base_amount"], e["fx_source"]) == ("360.00", "manual")

        await c.patch(f"{FIN}/projects/{p['id']}", json={"base_currency": "USD"}, headers=h)

        row = (await c.get(f"{FIN}/projects/{p['id']}/expenses", headers=h)).json()["items"][0]
        # 360 BYN * 0.25 USD/BYN = 90.00 USD; still flagged as the owner's own rate.
        assert row["base_amount"] == "90.00"
        assert row["fx_source"] == "manual"
        assert row["fx_rate"] == "0.9"


async def test_base_change_without_a_rate_leaves_the_expense_unpriced_not_wrong(db_ready, fake_fx):
    fake_fx.set("EUR", MARCH_29, "1.10")
    async with await client() as c:
        h = await register(c)
        p = await make_project(c, h, "X", "USD")
        await add_ok(c, h, p["id"], "100", "EUR", MARCH_29)

        out = (await c.patch(f"{FIN}/projects/{p['id']}", json={"base_currency": "KZT"}, headers=h)).json()

        assert out["unpriced_count"] == 1 and out["total_base"] == "0.00"
        row = (await c.get(f"{FIN}/projects/{p['id']}/expenses", headers=h)).json()["items"][0]
        assert row["base_amount"] is None and row["fx_rate"] is None and row["fx_source"] is None


async def test_patch_with_the_same_base_currency_does_not_reprice(db_ready, fake_fx):
    fake_fx.set("EUR", MARCH_29, "1.10")
    async with await client() as c:
        h = await register(c)
        p = await make_project(c, h, "X", "USD")
        await add_ok(c, h, p["id"], "100", "EUR", MARCH_29)
        before = fake_fx.call_count

        await c.patch(f"{FIN}/projects/{p['id']}", json={"base_currency": "USD", "name": "Y"}, headers=h)

        assert fake_fx.call_count == before
