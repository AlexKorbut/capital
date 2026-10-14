"""fin-01 review follow-ups: provider failures vs "no data", per-user write limits,
one-session rate storage, text normalisation, encryption at rest, conditional revalue."""
from __future__ import annotations

import time
import unicodedata
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError

from core.db import SessionLocal, engine
from models.finance import FinExpense, FinProject, FxRateDaily
from models.user import User
from services import finance as finance_service
from services import fx_history
from tests.finance_client import API, FIN, add_expense, add_ok, client, make_project, register

FRI = "2024-03-29"
THU = "2024-03-28"


async def _seed_rate(ccy: str, on: str, usd: str, source: str = "ecb") -> None:
    async with SessionLocal() as db:
        db.add(
            FxRateDaily(
                rate_date=date.fromisoformat(on),
                currency=ccy,
                usd_per_unit=Decimal(usd),
                source=source,
                fetched_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()


async def _setup(c, base: str = "USD"):
    h = await register(c)
    return h, await make_project(c, h, "Машина", base)


# --- M2: a failing provider means "no rate", never a quiet stored one --------------------


async def test_provider_outage_saves_the_expense_unpriced_despite_a_fresh_stored_rate(db_ready, fake_fx):
    await _seed_rate("EUR", THU, "1.0500")
    fake_fx.fail_all()
    async with await client() as c:
        h, p = await _setup(c)

        e = (await add_expense(c, h, p["id"], "100", "EUR", FRI)).json()

        assert e["base_amount"] is None and e["fx_rate"] is None
        assert e["fx_date"] is None and e["fx_source"] is None

        # The provider comes back: revalue completes the job with the real rate of the day.
        fake_fx.recover()
        fake_fx.set("EUR", FRI, "1.10")
        done = (await c.post(f"{FIN}/projects/{p['id']}/revalue", headers=h)).json()
        assert done == {"revalued": 1, "unpriced_count": 0}
        row = (await c.get(f"{FIN}/projects/{p['id']}/expenses", headers=h)).json()["items"][0]
        assert (row["base_amount"], row["fx_source"], row["fx_date"]) == ("110.00", "ecb", FRI)


async def test_provider_no_data_uses_the_stored_rate_with_its_own_date(db_ready, fake_fx):
    await _seed_rate("EUR", THU, "1.0500")  # the provider is up but has nothing for FRI (404)
    async with await client() as c:
        h, p = await _setup(c)

        e = (await add_expense(c, h, p["id"], "100", "EUR", FRI)).json()

        assert (e["base_amount"], e["fx_date"], e["fx_source"]) == ("105.00", THU, "ecb")


# --- M3: base change under a time budget ---------------------------------------------------


async def test_base_change_that_runs_out_of_time_reports_unpriced_and_revalue_finishes(db_ready, fake_fx, monkeypatch):
    days = ["2024-03-25", "2024-03-26", "2024-03-27", "2024-03-28"]
    for day in days:
        fake_fx.set("EUR", day, "1.10")
    async with await client() as c:
        h, p = await _setup(c, "USD")
        for day in days:
            await add_ok(c, h, p["id"], "100", "EUR", day)  # priced, EUR rates now stored

        monkeypatch.setattr(fx_history, "FX_BUDGET_SECONDS", 0.3)
        fake_fx.hang()  # the BYN provider never answers
        started = time.monotonic()
        res = await c.patch(f"{FIN}/projects/{p['id']}", json={"base_currency": "BYN"}, headers=h)

        assert res.status_code == 200, res.text
        assert time.monotonic() - started < 1.5
        out = res.json()
        assert out["base_currency"] == "BYN"
        assert out["unpriced_count"] == 4 and out["total_base"] == "0.00"

        fake_fx.recover()
        for day in days:
            fake_fx.set("BYN", day, "0.25")
        done = (await c.post(f"{FIN}/projects/{p['id']}/revalue", headers=h)).json()
        assert done == {"revalued": 4, "unpriced_count": 0}
        after = (await c.get(f"{FIN}/projects/{p['id']}", headers=h)).json()
        assert after["total_base"] == "1760.00" and after["unpriced_count"] == 0  # 4 x 100 x 1.10 / 0.25


# --- M4: write limits are per user, not per IP ---------------------------------------------


async def _hammer(call, limit: int = 60):
    codes = [(await call(i)).status_code for i in range(limit + 1)]
    assert codes[:limit] == [200 if codes[0] == 200 else 201] * limit, codes
    return codes[-1]


async def test_project_updates_are_limited_per_user_and_shared_between_jwt_and_token(db_ready):
    async with await client() as c:
        alice, bob = await register(c), await register(c)
        pa = await make_project(c, alice, "A")
        pb = await make_project(c, bob, "B")
        token = (await c.post(f"{API}/account/tokens", json={"name": "jarvis"}, headers=alice)).json()["token"]
        by_token = {"Authorization": f"Bearer {token}"}

        async def patch(i: int):  # alternate credentials: still ONE user
            headers = alice if i % 2 == 0 else by_token
            return await c.patch(f"{FIN}/projects/{pa['id']}", json={"note": f"n{i}"}, headers=headers)

        assert await _hammer(patch) == 429
        # Same IP, other user: untouched.
        ok = await c.patch(f"{FIN}/projects/{pb['id']}", json={"note": "fine"}, headers=bob)
        assert ok.status_code == 200
        # Reads are not limited by the write limit.
        assert (await c.get(f"{FIN}/projects/{pa['id']}", headers=alice)).status_code == 200


async def test_expense_creation_is_limited_per_user(db_ready, fake_fx):
    async with await client() as c:
        alice, bob = await register(c), await register(c)
        pa = await make_project(c, alice, "A")
        pb = await make_project(c, bob, "B")

        async def create(i: int):
            return await add_expense(c, alice, pa["id"], "1", "USD", FRI)

        assert await _hammer(create) == 429
        assert (await add_expense(c, bob, pb["id"], "1", "USD", FRI)).status_code == 201


async def test_expense_edits_are_limited_per_user(db_ready, fake_fx):
    async with await client() as c:
        alice, bob = await register(c), await register(c)
        pa = await make_project(c, alice, "A")
        pb = await make_project(c, bob, "B")
        ea = await add_ok(c, alice, pa["id"], "1", "USD", FRI)
        eb = await add_ok(c, bob, pb["id"], "1", "USD", FRI)

        async def edit(i: int):
            return await c.patch(f"{FIN}/expenses/{ea['id']}", json={"note": f"n{i}"}, headers=alice)

        assert await _hammer(edit) == 429
        assert (await c.patch(f"{FIN}/expenses/{eb['id']}", json={"note": "x"}, headers=bob)).status_code == 200


async def test_project_creation_is_limited_per_user(db_ready):
    async with await client() as c:
        alice, bob = await register(c), await register(c)

        async def create(i: int):
            return await c.post(f"{FIN}/projects", json={"name": f"P{i}", "base_currency": "USD"}, headers=alice)

        assert await _hammer(create) == 429
        assert (await c.post(f"{FIN}/projects", json={"name": "B1"}, headers=bob)).status_code == 201


async def test_revalue_and_fx_preview_limits_are_per_user_too(db_ready, fake_fx):
    async with await client() as c:
        alice, bob = await register(c), await register(c)
        pa = await make_project(c, alice, "A")
        pb = await make_project(c, bob, "B")
        for _ in range(10):
            assert (await c.post(f"{FIN}/projects/{pa['id']}/revalue", headers=alice)).status_code == 200
        assert (await c.post(f"{FIN}/projects/{pa['id']}/revalue", headers=alice)).status_code == 429
        assert (await c.post(f"{FIN}/projects/{pb['id']}/revalue", headers=bob)).status_code == 200

        for _ in range(60):
            assert (await c.get(f"{FIN}/fx?currency=USD&base=USD", headers=alice)).status_code == 200
        assert (await c.get(f"{FIN}/fx?currency=USD&base=USD", headers=alice)).status_code == 429
        assert (await c.get(f"{FIN}/fx?currency=USD&base=USD", headers=bob)).status_code == 200


# --- M5: rates ride in the request's own session ---------------------------------------------


async def test_a_request_with_rate_lookups_uses_exactly_one_pooled_connection(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    async with await client() as c:
        h, p = await _setup(c, "USD")
        in_use = 0
        peak = 0

        def _out(*_):
            nonlocal in_use, peak
            in_use += 1
            peak = max(peak, in_use)

        def _back(*_):
            nonlocal in_use
            in_use -= 1

        event.listen(engine.sync_engine, "checkout", _out)
        event.listen(engine.sync_engine, "checkin", _back)
        try:
            res = await add_expense(c, h, p["id"], "100", "EUR", FRI)  # provider fetch + save
        finally:
            event.remove(engine.sync_engine, "checkout", _out)
            event.remove(engine.sync_engine, "checkin", _back)

        assert res.status_code == 201 and res.json()["base_amount"] == "110.00"
        assert peak == 1  # no second session for the rate table
    async with SessionLocal() as db:
        assert [(r.currency, r.source) for r in await db.scalars(select(FxRateDaily))] == [("EUR", "ecb")]


# --- L2: normalise first, then measure; no NUL; no parameter leaks ----------------------------


NUL = "a\u0000b"


@pytest.mark.parametrize("field", ["name", "note"])
async def test_project_text_with_a_nul_byte_is_rejected(db_ready, field):
    async with await client() as c:
        h = await register(c)
        body = {"name": "ok", "base_currency": "USD", field: NUL}
        assert (await c.post(f"{FIN}/projects", json=body, headers=h)).status_code == 422
        p = await make_project(c, h, "fine")
        assert (await c.patch(f"{FIN}/projects/{p['id']}", json={field: NUL}, headers=h)).status_code == 422


@pytest.mark.parametrize("field", ["note", "category"])
async def test_expense_text_with_a_nul_byte_is_rejected(db_ready, fake_fx, field):
    async with await client() as c:
        h, p = await _setup(c)
        assert (await add_expense(c, h, p["id"], "1", "USD", FRI, **{field: NUL})).status_code == 422
        e = await add_ok(c, h, p["id"], "1", "USD", FRI)
        res = await c.patch(f"{FIN}/expenses/{e['id']}", json={field: NUL}, headers=h)
        assert res.status_code == 422


async def test_a_nul_byte_in_the_category_filter_is_rejected(db_ready):
    async with await client() as c:
        h, p = await _setup(c)
        res = await c.get(f"{FIN}/projects/{p['id']}/expenses?category=a%00b", headers=h)
        assert res.status_code == 422


async def test_project_name_is_measured_after_normalisation(db_ready):
    spaced = "а" * 60 + " " * 80 + "б" * 50  # 190 raw characters, 111 once whitespace is collapsed
    async with await client() as c:
        h = await register(c)
        ok = await c.post(f"{FIN}/projects", json={"name": spaced, "base_currency": "USD"}, headers=h)
        assert ok.status_code == 201, ok.text
        assert ok.json()["name"] == "а" * 60 + " " + "б" * 50
        too_long = await c.post(f"{FIN}/projects", json={"name": "я" * 121}, headers=h)
        assert too_long.status_code == 422
        tidy = await c.post(f"{FIN}/projects", json={"name": "  Моя \t  машина \n"}, headers=h)
        assert tidy.json()["name"] == "Моя машина"
        again = await c.post(f"{FIN}/projects", json={"name": "Моя машина"}, headers=h)
        assert again.status_code == 409  # same name once normalised


async def test_category_is_measured_after_normalisation(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c)
        spaced = "к" * 30 + " " * 40 + "л" * 29  # 99 raw, 60 normalised: fits
        ok = await add_expense(c, h, p["id"], "1", "USD", FRI, category=spaced)
        assert ok.status_code == 201, ok.text
        assert ok.json()["category"] == "к" * 30 + " " + "л" * 29
        # 61 normalised characters: refused.
        assert (await add_expense(c, h, p["id"], "1", "USD", FRI, category="к" * 61)).status_code == 422
        # Lower-casing can lengthen text ("İ" -> "i" + combining dot): measured after it.
        assert (await add_expense(c, h, p["id"], "1", "USD", FRI, category="İ" * 40)).status_code == 422
        assert (await add_expense(c, h, p["id"], "1", "USD", FRI, category="İ" * 30)).status_code == 201


async def test_unicode_forms_of_one_category_are_one_category(db_ready, fake_fx):
    composed = "café"
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    async with await client() as c:
        h, p = await _setup(c)
        a = await add_ok(c, h, p["id"], "1", "USD", FRI, category=composed)
        b = await add_ok(c, h, p["id"], "1", "USD", FRI, category=decomposed)
        assert a["category"] == b["category"] == composed
        found = await c.get(f"{FIN}/projects/{p['id']}/expenses", params={"category": decomposed}, headers=h)
        assert found.json()["total"] == 2


async def test_note_is_measured_after_trimming(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c)
        ok = await add_expense(c, h, p["id"], "1", "USD", FRI, note="н" * 2000 + "   \n")
        assert ok.status_code == 201
        assert (await add_expense(c, h, p["id"], "1", "USD", FRI, note="н" * 2001)).status_code == 422


async def test_database_errors_do_not_carry_parameter_values(db_ready):
    secret = "Проект-ТАЙНА-9917"
    async with SessionLocal() as db:
        user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="x", base_currency="USD")
        db.add(user)
        await db.flush()
        db.add(FinProject(user_id=user.id, name=secret, base_currency="USD"))
        await db.commit()
        db.add(FinProject(user_id=user.id, name=secret, base_currency="USD"))  # duplicate name
        with pytest.raises(IntegrityError) as caught:
            await db.commit()

    assert secret not in str(caught.value)
    assert "hide_parameters" in str(caught.value)
    assert engine.sync_engine.hide_parameters is True


# --- L5: revalue never overwrites a price that appeared in the meantime -------------------------


async def test_revalue_does_not_overwrite_a_manual_price_set_while_it_was_resolving(db_ready, fake_fx, monkeypatch):
    async with await client() as c:
        h, p = await _setup(c)
        e = await add_ok(c, h, p["id"], "100", "EUR", FRI)  # no rate yet
        assert e["base_amount"] is None
        fake_fx.set("EUR", FRI, "1.10")

        real_price = finance_service.Pricer.price

        async def _price_then_owner_edits(self, **kw):
            priced = await real_price(self, **kw)
            async with SessionLocal() as other:  # the owner prices it by hand meanwhile
                await other.execute(
                    text(
                        "UPDATE fin_expenses SET base_amount = 150.00, fx_rate = 1.5, fx_source = 'manual' "
                        "WHERE id = :id"
                    ).bindparams(id=uuid.UUID(e["id"]).hex)
                )
                await other.commit()
            return priced

        monkeypatch.setattr(finance_service.Pricer, "price", _price_then_owner_edits)

        res = await c.post(f"{FIN}/projects/{p['id']}/revalue", headers=h)

        assert res.json() == {"revalued": 0, "unpriced_count": 0}
        row = (await c.get(f"{FIN}/projects/{p['id']}/expenses", headers=h)).json()["items"][0]
        assert (row["base_amount"], row["fx_source"], row["fx_rate"]) == ("150.00", "manual", "1.5")


async def test_revalue_still_prices_the_untouched_expenses(db_ready, fake_fx):
    async with await client() as c:
        h, p = await _setup(c)
        await add_ok(c, h, p["id"], "100", "EUR", FRI)
        await add_ok(c, h, p["id"], "50", "EUR", FRI)
        fake_fx.set("EUR", FRI, "1.10")

        res = await c.post(f"{FIN}/projects/{p['id']}/revalue", headers=h)

        assert res.json() == {"revalued": 2, "unpriced_count": 0}
        assert (await c.get(f"{FIN}/projects/{p['id']}", headers=h)).json()["total_base"] == "165.00"


# --- L8: notes are ciphertext in the database --------------------------------------------------


@pytest.fixture
def fernet_on():
    from cryptography.fernet import Fernet

    from core import crypto
    from core.config import settings

    previous = settings.fernet_key
    settings.fernet_key = Fernet.generate_key().decode()
    crypto._fernet.cache_clear()
    try:
        yield
    finally:
        settings.fernet_key = previous
        crypto._fernet.cache_clear()


async def test_notes_are_stored_encrypted_and_read_back_decrypted(db_ready, fake_fx, fernet_on):
    project_note = "СЕКРЕТНАЯ заметка проекта 4417"
    expense_note = "СЕКРЕТНАЯ заметка траты 8821"
    async with await client() as c:
        h = await register(c)
        p = await make_project(c, h, "Машина", "USD", note=project_note)
        e = await add_ok(c, h, p["id"], "10", "USD", FRI, note=expense_note)

        async with SessionLocal() as db:  # what is actually on disk
            raw_project = (await db.execute(text("SELECT note FROM fin_projects"))).scalar_one()
            raw_expense = (await db.execute(text("SELECT note FROM fin_expenses"))).scalar_one()
        for raw, plain in ((raw_project, project_note), (raw_expense, expense_note)):
            assert plain not in raw
            assert raw.startswith("gAAAA")  # a Fernet token

        got = (await c.get(f"{FIN}/projects/{p['id']}", headers=h)).json()
        assert got["note"] == project_note
        row = (await c.get(f"{FIN}/projects/{p['id']}/expenses", headers=h)).json()["items"][0]
        assert row["note"] == expense_note and row["id"] == e["id"]


# --- the model-level encrypted columns still hold ciphertext through the ORM ------------------------


async def test_orm_roundtrip_with_encryption_on(db_ready, fernet_on):
    async with SessionLocal() as db:
        user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="x", base_currency="USD")
        db.add(user)
        await db.flush()
        db.add(FinProject(user_id=user.id, name="P", base_currency="USD", note="тайна"))
        await db.commit()
        db.add(
            FinExpense(
                user_id=user.id,
                project_id=(await db.scalar(select(FinProject.id))),
                amount=Decimal("1"),
                currency="USD",
                spent_on=date(2024, 3, 29),
                note="тайна траты",
            )
        )
        await db.commit()
    async with SessionLocal() as db:
        assert (await db.scalar(select(FinProject.note))) == "тайна"
        assert (await db.scalar(select(FinExpense.note))) == "тайна траты"
