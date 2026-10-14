"""Account export and deletion cover the finance data (and the goals/wallets they were missing)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import event, func, select

from core.db import SessionLocal, engine
from models.finance import FinExpense, FinProject, FxRateDaily
from models.goal import Goal
from models.scenario import Scenario
from models.snapshot import Snapshot
from models.wallet import Wallet
from tests.finance_client import API, FIN, add_ok, client, make_project

FRI = "2024-03-29"
BTC_ADDRESS = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"


async def _register_with_email(c) -> tuple[dict[str, str], uuid.UUID, str]:
    email = f"{uuid.uuid4().hex}@example.com"
    res = await c.post(f"{API}/auth/register", json={"email": email, "password": "supersecret1"})
    assert res.status_code == 201, res.text
    data = res.json()
    headers = {"Authorization": f"Bearer {data['tokens']['access_token']}"}
    me = (await c.get(f"{API}/auth/me", headers=headers)).json()
    return headers, uuid.UUID(me["id"]), email


async def _give_goal_and_wallet(user_id: uuid.UUID, title: str, label: str) -> None:
    async with SessionLocal() as db:
        db.add(Goal(user_id=user_id, title=title, target_usd=Decimal("100000")))
        db.add(Wallet(user_id=user_id, chain="BTC", address=BTC_ADDRESS, label=label))
        await db.commit()


async def _count(model, user_id: uuid.UUID) -> int:
    async with SessionLocal() as db:
        return await db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id))


async def test_export_contains_finance_goals_and_wallets_of_the_caller_only(db_ready, fake_fx):
    fake_fx.set("EUR", FRI, "1.10")
    async with await client() as c:
        alice, alice_id, _ = await _register_with_email(c)
        bob, bob_id, _ = await _register_with_email(c)
        car = await make_project(c, alice, "Машина", "USD", note="личное")
        await add_ok(c, alice, car["id"], "100", "EUR", FRI, category="топливо", note="полный бак")
        await _give_goal_and_wallet(alice_id, "Подушка", "холодный")
        other = await make_project(c, bob, "Чужой проект")
        await add_ok(c, bob, other["id"], "7", "USD", FRI)
        await _give_goal_and_wallet(bob_id, "Чужая цель", "чужой кошелёк")

        res = await c.get(f"{API}/account/export", headers=alice)

        assert res.status_code == 200, res.text
        dump = res.json()
        assert [p["name"] for p in dump["fin_projects"]] == ["Машина"]
        assert dump["fin_projects"][0]["note"] == "личное"
        assert dump["fin_projects"][0]["id"] == car["id"]
        [expense] = dump["fin_expenses"]
        assert Decimal(expense["amount"]) == 100 and Decimal(expense["base_amount"]) == Decimal("110.00")
        assert expense["fx_source"] == "ecb"
        assert expense["note"] == "полный бак" and expense["project_id"] == car["id"]
        assert [g["title"] for g in dump["goals"]] == ["Подушка"]
        assert [w["label"] for w in dump["wallets"]] == ["холодный"]
        assert dump["wallets"][0]["address"] == BTC_ADDRESS
        everything = res.text
        for foreign in ("Чужой проект", "Чужая цель", "чужой кошелёк"):
            assert foreign not in everything


async def test_export_of_a_user_without_any_of_it_has_empty_lists(db_ready):
    async with await client() as c:
        headers, _, _ = await _register_with_email(c)
        dump = (await c.get(f"{API}/account/export", headers=headers)).json()
        for key in ("fin_projects", "fin_expenses", "goals", "wallets"):
            assert dump[key] == [], key


async def test_deleting_the_account_removes_finance_goals_and_wallets(db_ready, fake_fx):
    async with await client() as c:
        alice, alice_id, alice_email = await _register_with_email(c)
        bob, bob_id, _ = await _register_with_email(c)
        for headers, name in ((alice, "A"), (bob, "B")):
            project = await make_project(c, headers, name)
            await add_ok(c, headers, project["id"], "10", "USD", FRI)
            await add_ok(c, headers, project["id"], "20", "USD", FRI)
        await _give_goal_and_wallet(alice_id, "g", "w")
        await _give_goal_and_wallet(bob_id, "g", "w")
        async with SessionLocal() as db:  # shared rate cache: not user data
            db.add(
                FxRateDaily(
                    rate_date=datetime(2024, 3, 29).date(),
                    currency="EUR",
                    usd_per_unit=Decimal("1.1"),
                    source="ecb",
                    fetched_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

        res = await c.request("DELETE", f"{API}/account", json={"confirm_email": alice_email}, headers=alice)
        assert res.status_code == 204, res.text

        for model in (FinProject, FinExpense, Goal, Wallet):
            assert await _count(model, alice_id) == 0, model.__name__
        assert await _count(FinProject, bob_id) == 1
        assert await _count(FinExpense, bob_id) == 2
        assert await _count(Goal, bob_id) == 1
        assert await _count(Wallet, bob_id) == 1
        async with SessionLocal() as db:
            assert await db.scalar(select(func.count()).select_from(FxRateDaily)) == 1
        # And Bob can still use the module.
        listed = await c.get(f"{FIN}/projects", headers=bob)
        assert [p["name"] for p in listed.json()] == ["B"]


async def test_a_wrong_confirmation_deletes_nothing(db_ready, fake_fx):
    async with await client() as c:
        alice, alice_id, _ = await _register_with_email(c)
        project = await make_project(c, alice, "A")
        await add_ok(c, alice, project["id"], "10", "USD", FRI)

        res = await c.request("DELETE", f"{API}/account", json={"confirm_email": "nope@example.com"}, headers=alice)

        assert res.status_code == 400
        assert await _count(FinProject, alice_id) == 1 and await _count(FinExpense, alice_id) == 1


async def _give_scenario(user_id: uuid.UUID, name: str, snapshot_id: uuid.UUID | None = None) -> None:
    async with SessionLocal() as db:
        db.add(
            Scenario(
                user_id=user_id,
                name=name,
                description="что если",
                base_snapshot_id=snapshot_id,
                changes=[{"op": "sell", "asset": "BTC"}],
                result_total_usd=Decimal("1234.50"),
            )
        )
        await db.commit()


async def test_export_contains_the_callers_scenarios_only(db_ready):
    async with await client() as c:
        alice, alice_id, _ = await _register_with_email(c)
        _, bob_id, _ = await _register_with_email(c)
        await _give_scenario(alice_id, "Продать биткоин")
        await _give_scenario(bob_id, "Чужой сценарий")

        res = await c.get(f"{API}/account/export", headers=alice)

        dump = res.json()
        assert [x["name"] for x in dump["scenarios"]] == ["Продать биткоин"]
        assert dump["scenarios"][0]["changes"] == [{"op": "sell", "asset": "BTC"}]
        assert Decimal(dump["scenarios"][0]["result_total_usd"]) == Decimal("1234.50")
        assert "Чужой сценарий" not in res.text


async def test_deleting_the_account_removes_scenarios_and_keeps_other_users(db_ready):
    async with await client() as c:
        alice, alice_id, alice_email = await _register_with_email(c)
        _, bob_id, _ = await _register_with_email(c)
        await _give_scenario(alice_id, "A")
        await _give_scenario(bob_id, "B")

        res = await c.request("DELETE", f"{API}/account", json={"confirm_email": alice_email}, headers=alice)

        assert res.status_code == 204, res.text
        assert await _count(Scenario, alice_id) == 0
        assert await _count(Scenario, bob_id) == 1


async def test_account_deletion_respects_foreign_keys_when_the_database_enforces_them(db_ready, fake_fx):
    """Postgres enforces FKs (SQLite here does not by default): a scenario points at a
    snapshot, so it has to go first. Turn enforcement on for this test and delete."""

    def _enforce(dbapi_connection, connection_record, connection_proxy):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with await client() as c:
        alice, alice_id, alice_email = await _register_with_email(c)
        project = await make_project(c, alice, "Машина")
        await add_ok(c, alice, project["id"], "10", "USD", FRI)
        async with SessionLocal() as db:
            snapshot = Snapshot(user_id=alice_id, is_confirmed=True)
            db.add(snapshot)
            await db.commit()
            snapshot_id = snapshot.id
        await _give_scenario(alice_id, "по снимку", snapshot_id)
        await _give_goal_and_wallet(alice_id, "g", "w")

        await engine.dispose()  # drop pooled connections so every new one enforces FKs
        event.listen(engine.sync_engine, "checkout", _enforce)
        try:
            res = await c.request("DELETE", f"{API}/account", json={"confirm_email": alice_email}, headers=alice)
        finally:
            event.remove(engine.sync_engine, "checkout", _enforce)
            await engine.dispose()

        assert res.status_code == 204, res.text
    for model in (FinProject, FinExpense, Goal, Wallet, Scenario, Snapshot):
        assert await _count(model, alice_id) == 0, model.__name__
