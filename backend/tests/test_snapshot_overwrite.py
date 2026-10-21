"""One snapshot per local day (default Europe/Minsk), and an explicit overwrite.

Creating a second snapshot for the same date without ``overwrite`` is a 409 and
writes nothing. ``overwrite=true`` replaces the row in one transaction: same id
(advice and scenarios stay linked), new assets and total. Charts and returns
are derived from those rows, so they follow the new total. A failed commit
rolls the replacement back.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import subprocess
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.graph import compile_all
from agents.state import AssetItem
from core.calendar import local_today
from core.db import SessionLocal, engine
from models.advice import AdviceItem, AdviceSession
from models.asset import Asset
from models.scenario import Scenario
from models.snapshot import Snapshot
from models.user import User
from models.wallet import Wallet
from services import portfolio_read, returns as returns_service
from services.persistence import (
    SnapshotDateConflict,
    find_snapshot,
    save_portfolio,
)

API = "/api/v1"


def _cash(amount: str, note: str, usd: str | None = None) -> AssetItem:
    return AssetItem(
        asset_type="cash",
        amount=Decimal(amount),
        currency="USD",
        note=note,
        usd_value=Decimal(usd) if usd is not None else Decimal(amount),
        usd_rate=Decimal(1),
        confidence=1,
    )


async def _user() -> uuid.UUID:
    uid = uuid.uuid4()
    async with SessionLocal() as session:
        session.add(User(id=uid, email=f"{uid}@t.test", password_hash="x", base_currency="USD"))
        await session.commit()
    return uid


def _at(day: date, hour: int = 12) -> datetime:
    return datetime.combine(day, time(hour, 0), tzinfo=timezone.utc)


# --- service -----------------------------------------------------------------


async def test_create_snapshot_for_a_day(db_ready):
    uid = await _user()
    snapshot_id = await save_portfolio(
        user_id=uid,
        assets=[_cash("1000", "first")],
        total_usd=Decimal("1000.00"),
        raw_input="1000 долларов",
    )

    async with SessionLocal() as session:
        snap = await session.get(Snapshot, uuid.UUID(snapshot_id))
        assert snap is not None
        assert snap.snapshot_date == local_today()
        assert snap.total_usd == Decimal("1000.00")
        assert snap.is_confirmed is True
        notes = list(await session.scalars(select(Asset.note).where(Asset.snapshot_id == snap.id)))
        assert notes == ["first"]

        def uniques(sync_conn):
            insp = inspect(sync_conn)
            found = [set(u["column_names"]) for u in insp.get_unique_constraints("snapshots")]
            found += [set(i["column_names"]) for i in insp.get_indexes("snapshots") if i["unique"]]
            return found

        async with engine.connect() as conn:
            assert {"user_id", "snapshot_date"} in await conn.run_sync(uniques)


async def test_same_day_without_overwrite_is_a_conflict_and_keeps_the_original(db_ready):
    uid = await _user()
    original = await save_portfolio(
        user_id=uid, assets=[_cash("1000", "original")], total_usd=Decimal("1000")
    )

    with pytest.raises(SnapshotDateConflict) as caught:
        await save_portfolio(
            user_id=uid, assets=[_cash("2500", "replacement")], total_usd=Decimal("2500")
        )

    assert caught.value.snapshot_id == uuid.UUID(original)
    assert caught.value.snapshot_date == local_today()
    assert "уже есть" in caught.value.message

    async with SessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(Snapshot))
        assert count == 1
        notes = list(await session.scalars(select(Asset.note)))
        assert notes == ["original"]
        snap = await session.get(Snapshot, uuid.UUID(original))
        assert snap.total_usd == Decimal("1000")


async def test_other_day_and_other_user_do_not_conflict(db_ready):
    uid = await _user()
    other = await _user()
    yesterday = local_today() - timedelta(days=1)

    await save_portfolio(
        user_id=uid,
        assets=[_cash("100", "yesterday")],
        total_usd=Decimal("100"),
        snapshot_date=yesterday,
        created_at=_at(yesterday),
    )
    today_id = await save_portfolio(
        user_id=uid, assets=[_cash("200", "today")], total_usd=Decimal("200")
    )
    await save_portfolio(
        user_id=other, assets=[_cash("300", "other-user")], total_usd=Decimal("300")
    )

    async with SessionLocal() as session:
        rows = list(await session.scalars(select(Snapshot)))
        assert len(rows) == 3
        assert await find_snapshot(session, uid) is not None
        mine = await session.get(Snapshot, uuid.UUID(today_id))
        assert mine.snapshot_date == local_today()
        assert mine.total_usd == Decimal("200")


async def test_overwrite_replaces_assets_recalculates_and_keeps_links(db_ready):
    uid = await _user()
    yesterday = local_today() - timedelta(days=1)
    await save_portfolio(
        user_id=uid,
        assets=[_cash("100", "yesterday")],
        total_usd=Decimal("100.00"),
        snapshot_date=yesterday,
        created_at=_at(yesterday),
    )
    original = await save_portfolio(
        user_id=uid,
        assets=[_cash("200", "today-old")],
        total_usd=Decimal("200.00"),
        raw_input="old text",
        input_type="text",
    )
    original_id = uuid.UUID(original)

    async with SessionLocal() as session:
        snap = await session.get(Snapshot, original_id)
        snap.note = "не терять"
        created_at = snap.created_at
        advice = AdviceSession(id=uuid.uuid4(), user_id=uid, snapshot_id=original_id)
        session.add(advice)
        session.add(
            AdviceItem(
                id=uuid.uuid4(),
                session_id=advice.id,
                user_id=uid,
                title="концентрация",
                body="текст совета",
            )
        )
        session.add(
            Scenario(
                id=uuid.uuid4(),
                user_id=uid,
                name="что если",
                base_snapshot_id=original_id,
            )
        )
        await session.commit()
        advice_id = advice.id

    replaced = await save_portfolio(
        user_id=uid,
        assets=[_cash("50", "today-new")],
        total_usd=Decimal("50.00"),
        raw_input="new text",
        input_type="import",
        overwrite=True,
    )
    assert replaced == original

    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Snapshot)) == 2
        snap = await session.get(Snapshot, original_id)
        assert snap.total_usd == Decimal("50.00")
        assert snap.note == "не терять"
        assert snap.raw_input == "new text"
        assert snap.input_type == "import"
        assert snap.created_at == created_at
        assert snap.updated_at is not None and snap.updated_at >= created_at
        assert snap.snapshot_date == local_today()

        notes = list(
            await session.scalars(select(Asset.note).where(Asset.snapshot_id == original_id))
        )
        assert notes == ["today-new"]

        advice = await session.get(AdviceSession, advice_id)
        assert advice.snapshot_id == original_id
        bodies = list(await session.scalars(select(AdviceItem.body)))
        assert bodies == ["текст совета"]
        scenario = await session.scalar(select(Scenario).where(Scenario.user_id == uid))
        assert scenario.base_snapshot_id == original_id

        history = await portfolio_read.history_chart(session, uid)
        assert [point["total_usd"] for point in history] == ["100.00", "50.00"]
        growth = await returns_service.compute_returns(session, uid)
        assert growth["snapshots_count"] == 2
        assert growth["current_usd"] == "50.00"


async def test_overwrite_commit_failure_rolls_back_the_whole_replacement(db_ready, monkeypatch):
    uid = await _user()
    original = await save_portfolio(
        user_id=uid, assets=[_cash("1000", "original")], total_usd=Decimal("1000")
    )
    original_id = uuid.UUID(original)

    async def fail_commit(self):
        # The replacement is already flushed inside this transaction...
        notes = list(await self.scalars(select(Asset.note).where(Asset.snapshot_id == original_id)))
        assert notes == ["replacement"]
        raise RuntimeError("commit failed")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="commit failed"):
        await save_portfolio(
            user_id=uid,
            assets=[_cash("2500", "replacement")],
            total_usd=Decimal("2500"),
            overwrite=True,
        )

    async with SessionLocal() as session:
        snap = await session.get(Snapshot, original_id)
        assert snap.total_usd == Decimal("1000")
        notes = list(await session.scalars(select(Asset.note)))
        assert notes == ["original"]
        assert await session.scalar(select(func.count()).select_from(Snapshot)) == 1


async def test_day_is_minsk_at_00_30_and_a_timezone_setting_overrides_it(db_ready):
    """00:30 in Minsk is still the previous UTC date. It must not overwrite yesterday."""
    night = datetime(2026, 10, 20, 21, 30, tzinfo=timezone.utc)  # 21.10.2026 00:30 +03
    evening = datetime(2026, 10, 20, 20, 59, tzinfo=timezone.utc)  # 20.10.2026 23:59 +03
    assert local_today(None, night) == date(2026, 10, 21)
    assert local_today(None, evening) == date(2026, 10, 20)
    assert local_today({"timezone": "UTC"}, night) == date(2026, 10, 20)
    assert local_today({"timezone": "Not/AZone"}, night) == date(2026, 10, 21)

    uid = await _user()
    await save_portfolio(
        user_id=uid,
        assets=[_cash("100", "evening")],
        snapshot_date=date(2026, 10, 20),
        created_at=evening,
        total_usd=Decimal("100"),
    )
    fresh = await save_portfolio(
        user_id=uid,
        assets=[_cash("50", "after-midnight")],
        total_usd=Decimal("50"),
        now=night,
    )
    async with SessionLocal() as session:
        snap = await session.get(Snapshot, uuid.UUID(fresh))
        assert snap.snapshot_date == date(2026, 10, 21)
        assert await session.scalar(select(func.count()).select_from(Snapshot)) == 2
        kept = await session.scalar(select(Asset.note).where(Asset.note == "evening"))
        assert kept == "evening"

    with pytest.raises(SnapshotDateConflict) as caught:
        await save_portfolio(user_id=uid, assets=[_cash("60", "again")], now=night)
    assert caught.value.snapshot_date == date(2026, 10, 21)

    async with SessionLocal() as session:
        user = await session.get(User, uid)
        user.settings = {"timezone": "UTC"}
        await session.commit()
    with pytest.raises(SnapshotDateConflict) as utc_conflict:
        await save_portfolio(user_id=uid, assets=[_cash("70", "utc-user")], now=night)
    assert utc_conflict.value.snapshot_date == date(2026, 10, 20)


async def test_wallet_replace_keeps_non_wallet_assets_and_bumps_updated_at(db_ready):
    uid = await _user()
    original = await save_portfolio(
        user_id=uid,
        assets=[
            _cash("1000", "наличные"),
            AssetItem(
                asset_type="bank_deposit", amount=Decimal("500"), currency="USD",
                note="вклад", usd_value=Decimal("500"), usd_rate=Decimal(1),
            ),
            AssetItem(
                asset_type="real_estate", amount=Decimal("2000"), currency="USD",
                note="квартира", usd_value=Decimal("2000"), usd_rate=Decimal(1),
            ),
            AssetItem(
                asset_type="crypto", amount=Decimal("1"), currency="BTC", symbol="BTC",
                note="ручной биток", usd_value=Decimal("100"),
            ),
            AssetItem(
                asset_type="crypto", amount=Decimal("0.1"), currency="BTC", symbol="BTC",
                note="старый кошелёк", wallet_address="bc1q-old", usd_value=Decimal("50"),
            ),
        ],
        total_usd=Decimal("3650"),
        raw_input="текст снимка",
        input_type="text",
    )
    async with SessionLocal() as session:
        before = await session.get(Snapshot, uuid.UUID(original))
        created_at = before.created_at
        before.updated_at = created_at - timedelta(days=3)
        before.note = "keep-note"
        await session.commit()

    replaced = await save_portfolio(
        user_id=uid,
        assets=[
            AssetItem(
                asset_type="crypto", amount=Decimal("2"), currency="BTC", symbol="BTC",
                quantity=Decimal("2"), wallet_address="bc1q-new",
                note="новый кошелёк", usd_value=Decimal("20"),
            )
        ],
        total_usd=Decimal("20"),
        raw_input="",
        input_type="import",
        overwrite=True,
        asset_replace="wallets",
    )
    assert replaced == original
    async with SessionLocal() as session:
        snap = await session.get(Snapshot, uuid.UUID(original))
        assert snap.note == "keep-note"
        assert snap.raw_input == "текст снимка"
        assert snap.input_type == "text"
        assert snap.created_at == created_at
        assert snap.updated_at > created_at - timedelta(days=3)
        assert snap.total_usd == Decimal("3620")
        notes = set(await session.scalars(select(Asset.note).where(Asset.snapshot_id == snap.id)))
        assert notes == {"наличные", "вклад", "квартира", "ручной биток", "новый кошелёк"}
        addresses = set(
            await session.scalars(select(Asset.wallet_address).where(Asset.snapshot_id == snap.id))
        )
        assert "bc1q-new" in addresses
        assert "bc1q-old" not in addresses


async def test_reminder_follows_updated_at(db_ready, monkeypatch):
    from services import digest

    sent: list[str] = []

    async def _fake_send(*, to: str, **_kwargs):
        sent.append(to)

    monkeypatch.setattr(digest.email_service, "send_reminder_email", _fake_send)
    fresh_id = uuid.uuid4()
    stale_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        fresh = User(
            id=fresh_id, email="fresh@t.test", password_hash="x",
            email_verified=True, base_currency="USD",
        )
        stale = User(
            id=stale_id, email="stale@t.test", password_hash="x",
            email_verified=True, base_currency="USD",
        )
        session.add_all([fresh, stale])
        session.add(Snapshot(
            id=uuid.uuid4(), user_id=fresh_id, is_confirmed=True,
            snapshot_date=date(2024, 1, 1), total_usd=Decimal("1"),
            created_at=now - timedelta(days=40), updated_at=now,
        ))
        session.add(Snapshot(
            id=uuid.uuid4(), user_id=stale_id, is_confirmed=True,
            snapshot_date=date(2024, 1, 2), total_usd=Decimal("1"),
            created_at=now, updated_at=now - timedelta(days=40),
        ))
        await session.commit()

    count = await digest.run_update_reminders()
    assert sent == ["stale@t.test"]
    assert count == 1


# --- HTTP: the flag is explicit, and a 409 leaves the thread resumable -------


@pytest.fixture
def graphs_on_app():
    from main import app

    app.state.graphs = compile_all(MemorySaver())
    return app


async def _register(client) -> tuple[dict[str, str], str]:
    res = await client.post(
        f"{API}/auth/register",
        json={"email": f"{uuid.uuid4().hex}@example.com", "password": "supersecret1"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    return {"Authorization": f"Bearer {body['tokens']['access_token']}"}, body["user"]["id"]


async def _preview(client, headers) -> str:
    res = await client.post(
        f"{API}/portfolio/input",
        json={"text": "1000 долларов наличными", "input_type": "text"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()["thread_id"]


def _confirm_assets(amount: str) -> list[dict]:
    return [
        {
            "asset_type": "cash",
            "amount": amount,
            "currency": "USD",
            "note": amount,
            "usd_value": amount,
            "usd_rate": "1",
            "confidence": 1,
        }
    ]


async def test_confirm_conflicts_without_flag_and_overwrites_with_it(db_ready, graphs_on_app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=graphs_on_app), base_url="http://t") as client:
        headers, _user_id = await _register(client)
        first_thread = await _preview(client, headers)
        created = await client.post(
            f"{API}/portfolio/confirm",
            json={"thread_id": first_thread, "assets": _confirm_assets("1000")},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        snapshot_id = created.json()["snapshot_id"]
        assert created.json()["total_usd"] in (None, "1000", "1000.00", "1000.0") or Decimal(
            created.json()["total_usd"]
        ) == Decimal("1000")

        second_thread = await _preview(client, headers)
        conflict = await client.post(
            f"{API}/portfolio/confirm",
            json={"thread_id": second_thread, "assets": _confirm_assets("2500")},
            headers=headers,
        )
        assert conflict.status_code == 409, conflict.text
        detail = conflict.json()["detail"]
        assert detail["code"] == "snapshot_exists"
        assert detail["snapshot_date"] == local_today().isoformat()
        assert detail["snapshot_id"] == snapshot_id
        assert detail["message"].startswith(f"Снимок за {local_today().strftime('%d.%m.%Y')}")
        # The flag was absent: the body must not have been overwritten.
        assert "overwrite" not in conflict.request.content.decode()

        current = await client.get(f"{API}/portfolio/current", headers=headers)
        assert current.json()["snapshot_id"] == snapshot_id
        assert Decimal(current.json()["total_usd"]) == Decimal("1000")
        assert [Decimal(a["amount"]) for a in current.json()["assets"]] == [Decimal("1000")]

        # Same pending thread, now with the explicit flag.
        replaced = await client.post(
            f"{API}/portfolio/confirm",
            json={
                "thread_id": second_thread,
                "assets": _confirm_assets("2500"),
                "overwrite": True,
            },
            headers=headers,
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["snapshot_id"] == snapshot_id
        assert b'"overwrite":true' in replaced.request.content or b'"overwrite": true' in replaced.request.content

        current = await client.get(f"{API}/portfolio/current", headers=headers)
        body = current.json()
        assert body["snapshot_id"] == snapshot_id
        assert Decimal(body["total_usd"]) == Decimal("2500")
        assert [Decimal(a["amount"]) for a in body["assets"]] == [Decimal("2500")]

        history = await client.get(f"{API}/portfolio/history/chart", headers=headers)
        assert history.status_code == 200
        points = history.json()
        assert len(points) == 1
        assert Decimal(points[0]["total_usd"]) == Decimal("2500")

        listed = await client.get(f"{API}/portfolio/snapshots", headers=headers)
        assert len(listed.json()) == 1
        assert listed.json()[0]["snapshot_id"] == snapshot_id


async def test_overwrite_does_not_spend_a_snapshot_quota_slot(db_ready, graphs_on_app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=graphs_on_app), base_url="http://t") as client:
        headers, user_id = await _register(client)
        uid = uuid.UUID(user_id)
        async with SessionLocal() as session:
            # Free plan: 3 confirmed snapshots this month. Two older days + today.
            for offset, day in enumerate((date(2020, 3, 1), date(2020, 3, 2), local_today())):
                session.add(
                    Snapshot(
                        id=uuid.uuid4(),
                        user_id=uid,
                        total_usd=Decimal("10"),
                        base_currency="USD",
                        is_confirmed=True,
                        snapshot_date=day,
                        created_at=datetime.now(timezone.utc),
                        note=f"seed-{offset}",
                    )
                )
            await session.commit()

        thread_id = await _preview(client, headers)
        blocked = await client.post(
            f"{API}/portfolio/confirm",
            json={"thread_id": thread_id, "assets": _confirm_assets("999")},
            headers=headers,
        )
        # Today's row exists, so the answer is the conflict — not the quota 403 —
        # and the thread is still open for an overwrite.
        assert blocked.status_code == 409, blocked.text

        replaced = await client.post(
            f"{API}/portfolio/confirm",
            json={
                "thread_id": thread_id,
                "assets": _confirm_assets("999"),
                "overwrite": True,
            },
            headers=headers,
        )
        assert replaced.status_code == 200, replaced.text

        async with SessionLocal() as session:
            count = await session.scalar(
                select(func.count()).select_from(Snapshot).where(Snapshot.user_id == uid)
            )
            assert count == 3


async def test_a_new_day_still_hits_the_monthly_quota(db_ready, graphs_on_app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=graphs_on_app), base_url="http://t") as client:
        headers, user_id = await _register(client)
        uid = uuid.UUID(user_id)
        async with SessionLocal() as session:
            for i in range(3):
                session.add(
                    Snapshot(
                        id=uuid.uuid4(),
                        user_id=uid,
                        total_usd=Decimal("10"),
                        base_currency="USD",
                        is_confirmed=True,
                        snapshot_date=date(2020, 4, 1) + timedelta(days=i),
                        created_at=datetime.now(timezone.utc),
                    )
                )
            await session.commit()

        thread_id = await _preview(client, headers)
        res = await client.post(
            f"{API}/portfolio/confirm",
            json={"thread_id": thread_id, "assets": _confirm_assets("1000")},
            headers=headers,
        )
        assert res.status_code == 403, res.text
        assert res.json()["detail"]["code"] == "quota_exceeded"


async def test_superseded_same_day_row_is_kept_but_absent_from_the_series(db_ready):
    """A pre-migration duplicate stays in the table and out of the chart."""
    uid = await _user()
    canonical = await save_portfolio(
        user_id=uid, assets=[_cash("200", "canonical")], total_usd=Decimal("200")
    )
    older_id = uuid.uuid4()
    async with SessionLocal() as session:
        session.add(
            Snapshot(
                id=older_id,
                user_id=uid,
                snapshot_date=local_today(),
                superseded_at=_at(local_today(), 9),
                created_at=_at(local_today(), 9),
                total_usd=Decimal("100"),
                base_currency="USD",
                is_confirmed=True,
                note="ранний за тот же день",
            )
        )
        session.add(
            Asset(
                id=uuid.uuid4(),
                snapshot_id=older_id,
                user_id=uid,
                asset_type="cash",
                amount=Decimal("100"),
                currency="USD",
                note="older-asset",
                usd_value=Decimal("100"),
            )
        )
        await session.commit()

    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Snapshot)) == 2
        older = await session.get(Snapshot, older_id)
        assert older.note == "ранний за тот же день"
        assert older.superseded_at is not None
        live = await find_snapshot(session, uid)
        assert live.id == uuid.UUID(canonical)
        history = await portfolio_read.history_chart(session, uid)
        assert [point["total_usd"] for point in history] == ["200.00"]
        growth = await returns_service.compute_returns(session, uid)
        assert growth["snapshots_count"] == 1
        assert growth["current_usd"] == "200.00"


async def test_wallet_sync_conflicts_then_overwrites_only_wallet_assets(
    db_ready, graphs_on_app, monkeypatch
):
    from decimal import Decimal as D
    from httpx import ASGITransport, AsyncClient

    import services.market as market
    import services.wallets as wallets_service

    async def _balance(_chain: str, _address: str):
        return D("2")

    async def _price(_symbol: str):
        return D("10")

    monkeypatch.setattr(wallets_service, "fetch_balance", _balance)
    monkeypatch.setattr(market, "usd_price_for_crypto", _price)

    async with AsyncClient(transport=ASGITransport(app=graphs_on_app), base_url="http://t") as client:
        headers, user_id = await _register(client)
        uid = uuid.UUID(user_id)
        snap_id = uuid.uuid4()
        async with SessionLocal() as session:
            session.add(Snapshot(
                id=snap_id, user_id=uid, snapshot_date=local_today(),
                total_usd=D("3650"), base_currency="USD", is_confirmed=True,
                input_type="text", raw_input="текст снимка", note="keep-note",
            ))
            session.add(Asset(
                id=uuid.uuid4(), snapshot_id=snap_id, user_id=uid, asset_type="cash",
                amount=D("1000"), currency="USD", note="наличные", usd_value=D("1000"),
            ))
            session.add(Asset(
                id=uuid.uuid4(), snapshot_id=snap_id, user_id=uid, asset_type="bank_deposit",
                amount=D("500"), currency="USD", note="вклад", usd_value=D("500"),
            ))
            session.add(Asset(
                id=uuid.uuid4(), snapshot_id=snap_id, user_id=uid, asset_type="real_estate",
                amount=D("2000"), currency="USD", note="квартира", usd_value=D("2000"),
            ))
            session.add(Asset(
                id=uuid.uuid4(), snapshot_id=snap_id, user_id=uid, asset_type="crypto",
                amount=D("1"), currency="BTC", symbol="BTC", note="ручной биток",
                usd_value=D("100"),
            ))
            session.add(Asset(
                id=uuid.uuid4(), snapshot_id=snap_id, user_id=uid, asset_type="crypto",
                amount=D("0.1"), currency="BTC", symbol="BTC", wallet_address="bc1q-old",
                note="старый кошелёк", usd_value=D("50"),
            ))
            session.add(Wallet(id=uuid.uuid4(), user_id=uid, chain="BTC", address="bc1q-new"))
            await session.commit()

        blocked = await client.post(f"{API}/wallets/sync", headers=headers)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "snapshot_exists"
        async with SessionLocal() as session:
            notes = set(await session.scalars(select(Asset.note)))
            assert "старый кошелёк" in notes and "наличные" in notes

        replaced = await client.post(f"{API}/wallets/sync", params={"overwrite": True}, headers=headers)
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["snapshot_id"] == str(snap_id)
        assert D(replaced.json()["total_usd"]) == D("3620")

        async with SessionLocal() as session:
            snap = await session.get(Snapshot, snap_id)
            assert snap.note == "keep-note"
            assert snap.raw_input == "текст снимка"
            assert snap.input_type == "text"
            notes = set(await session.scalars(select(Asset.note).where(Asset.snapshot_id == snap_id)))
            assert {"наличные", "вклад", "квартира", "ручной биток"} <= notes
            assert "старый кошелёк" not in notes
            addresses = set(
                await session.scalars(select(Asset.wallet_address).where(Asset.snapshot_id == snap_id))
            )
            assert "bc1q-new" in addresses
            assert "bc1q-old" not in addresses


# --- migration: same-day duplicates are marked, not deleted ------------------


def test_migration_marks_older_same_day_snapshots_and_keeps_them(tmp_path):
    db_path = tmp_path / "mig.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    env["FERNET_KEY"] = ""
    env["ENVIRONMENT"] = "development"
    backend = pathlib.Path(__file__).resolve().parents[1]

    subprocess.check_call(
        [sys.executable, "-m", "alembic", "upgrade", "b8c9d0e1f2a3"],
        cwd=backend,
        env=env,
    )

    user_a = uuid.uuid4().hex
    user_b = uuid.uuid4().hex
    early = uuid.uuid4().hex
    middle = uuid.uuid4().hex
    late = uuid.uuid4().hex
    other_day = uuid.uuid4().hex
    other_user_snap = uuid.uuid4().hex
    user_c = uuid.uuid4().hex
    iso_t = uuid.uuid4().hex
    space_later = uuid.uuid4().hex
    user_d = uuid.uuid4().hex
    minsk_early = uuid.uuid4().hex
    minsk_later = uuid.uuid4().hex
    minsk_previous = uuid.uuid4().hex
    advice_id = uuid.uuid4().hex
    scenario_id = uuid.uuid4().hex
    early_asset = uuid.uuid4().hex
    middle_asset = uuid.uuid4().hex
    kept_asset = uuid.uuid4().hex

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO users (id, email, base_currency, subscription, email_verified, settings, "
        "totp_enabled, token_version) VALUES (?, ?, 'USD', 'free', 0, '{}', 0, 0)",
        (user_a, f"{user_a}@t.test"),
    )
    conn.execute(
        "INSERT INTO users (id, email, base_currency, subscription, email_verified, settings, "
        "totp_enabled, token_version) VALUES (?, ?, 'USD', 'free', 0, '{}', 0, 0)",
        (user_b, f"{user_b}@t.test"),
    )
    conn.execute(
        "INSERT INTO snapshots (id, user_id, created_at, total_usd, is_confirmed, note) "
        "VALUES (?, ?, '2026-10-21 09:00:00', 100, 1, 'early-note')",
        (early, user_a),
    )
    conn.execute(
        "INSERT INTO snapshots (id, user_id, created_at, total_usd, is_confirmed, note) "
        "VALUES (?, ?, '2026-10-21 12:00:00', 150, 1, 'middle-note')",
        (middle, user_a),
    )
    conn.execute(
        "INSERT INTO snapshots (id, user_id, created_at, total_usd, is_confirmed, note) "
        "VALUES (?, ?, '2026-10-21 18:00:00', 250, 1, 'survivor-note')",
        (late, user_a),
    )
    conn.execute(
        "INSERT INTO snapshots (id, user_id, created_at, total_usd, is_confirmed) "
        "VALUES (?, ?, '2026-10-20 12:00:00', 80, 1)",
        (other_day, user_a),
    )
    conn.execute(
        "INSERT INTO snapshots (id, user_id, created_at, total_usd, is_confirmed) "
        "VALUES (?, ?, '2026-10-21 11:00:00', 5, 1)",
        (other_user_snap, user_b),
    )
    # ``T`` vs space: a string sort would keep the earlier T-row.
    conn.execute(
        "INSERT INTO users (id, email, base_currency, subscription, email_verified, settings, "
        "totp_enabled, token_version) VALUES (?, ?, 'USD', 'free', 0, '{}', 0, 0)",
        (user_c, f"{user_c}@t.test"),
    )
    conn.execute(
        "INSERT INTO snapshots (id, user_id, created_at, total_usd, is_confirmed, note) "
        "VALUES (?, ?, '2026-10-21T08:00:00', 1, 1, 'iso-t')",
        (iso_t, user_c),
    )
    conn.execute(
        "INSERT INTO snapshots (id, user_id, created_at, total_usd, is_confirmed, note) "
        "VALUES (?, ?, '2026-10-21 09:00:00', 2, 1, 'space-later')",
        (space_later, user_c),
    )
    # 00:30 Minsk on 21 Oct is 21:30 UTC on 20 Oct. UTC backfill would split the day.
    conn.execute(
        "INSERT INTO users (id, email, base_currency, subscription, email_verified, settings, "
        "totp_enabled, token_version) VALUES (?, ?, 'USD', 'free', 0, '{}', 0, 0)",
        (user_d, f"{user_d}@t.test"),
    )
    conn.execute(
        "INSERT INTO snapshots (id, user_id, created_at, total_usd, is_confirmed, note) "
        "VALUES (?, ?, '2026-10-20 21:30:00', 10, 1, '00:30-minsk')",
        (minsk_early, user_d),
    )
    conn.execute(
        "INSERT INTO snapshots (id, user_id, created_at, total_usd, is_confirmed, note) "
        "VALUES (?, ?, '2026-10-21 01:00:00', 20, 1, '04:00-minsk')",
        (minsk_later, user_d),
    )
    conn.execute(
        "INSERT INTO snapshots (id, user_id, created_at, total_usd, is_confirmed, note) "
        "VALUES (?, ?, '2026-10-20 20:59:00', 7, 1, '23:59-minsk')",
        (minsk_previous, user_d),
    )
    conn.execute(
        "INSERT INTO assets (id, snapshot_id, user_id, asset_type, note) VALUES (?, ?, ?, 'cash', ?)",
        (early_asset, early, user_a, "early-asset"),
    )
    conn.execute(
        "INSERT INTO assets (id, snapshot_id, user_id, asset_type, note) VALUES (?, ?, ?, 'cash', ?)",
        (middle_asset, middle, user_a, "middle-asset"),
    )
    conn.execute(
        "INSERT INTO assets (id, snapshot_id, user_id, asset_type, note) VALUES (?, ?, ?, 'cash', ?)",
        (kept_asset, late, user_a, "kept-asset"),
    )
    conn.execute(
        "INSERT INTO advice_sessions (id, user_id, snapshot_id) VALUES (?, ?, ?)",
        (advice_id, user_a, early),
    )
    conn.execute(
        "INSERT INTO scenarios (id, user_id, base_snapshot_id, name) VALUES (?, ?, ?, 'what-if')",
        (scenario_id, user_a, middle),
    )
    conn.commit()
    conn.close()

    subprocess.check_call(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=env,
    )

    conn = sqlite3.connect(db_path)
    snaps = conn.execute(
        "SELECT id, note, snapshot_date, total_usd, superseded_at FROM snapshots WHERE user_id = ?",
        (user_a,),
    ).fetchall()
    # All three same-day rows survive, plus the other day. Nothing was deleted.
    assert len(snaps) == 4
    by_id = {row[0]: row for row in snaps}
    assert set(by_id) == {early, middle, late, other_day}

    assert by_id[late][1] == "survivor-note"
    assert str(by_id[late][2]).startswith("2026-10-21")
    assert by_id[late][3] == 250
    assert by_id[late][4] is None  # canonical: occupies the unique slot

    for older in (early, middle):
        assert str(by_id[older][2]).startswith("2026-10-21")
        # Marked with the later snapshot's created_at, not deleted.
        assert str(by_id[older][4]).startswith("2026-10-21 18:00:00")
    assert by_id[early][1] == "early-note"
    assert by_id[middle][1] == "middle-note"
    assert by_id[other_day][4] is None
    assert str(by_id[other_day][2]).startswith("2026-10-20")

    assets = {
        row[0]
        for row in conn.execute("SELECT note FROM assets WHERE user_id = ?", (user_a,))
    }
    assert assets == {"early-asset", "middle-asset", "kept-asset"}

    advice = conn.execute(
        "SELECT snapshot_id FROM advice_sessions WHERE id = ?", (advice_id,)
    ).fetchone()
    assert advice[0] == early  # still points at the snapshot that was not deleted
    scenario = conn.execute(
        "SELECT base_snapshot_id FROM scenarios WHERE id = ?", (scenario_id,)
    ).fetchone()
    assert scenario[0] == middle

    other = conn.execute(
        "SELECT snapshot_date, superseded_at FROM snapshots WHERE id = ?",
        (other_user_snap,),
    ).fetchone()
    assert str(other[0]).startswith("2026-10-21")
    assert other[1] is None  # a different user is not a duplicate

    mixed = {
        row[0]: row
        for row in conn.execute(
            "SELECT id, note, superseded_at FROM snapshots WHERE user_id = ?",
            (user_c,),
        )
    }
    assert mixed[space_later][2] is None
    assert mixed[space_later][1] == "space-later"
    assert str(mixed[iso_t][2]).startswith("2026-10-21 09:00:00")
    assert mixed[iso_t][1] == "iso-t"

    updated = conn.execute(
        "SELECT updated_at FROM snapshots WHERE id = ?", (late,)
    ).fetchone()
    assert str(updated[0]).startswith("2026-10-21 18:00:00")

    minsk = {
        row[0]: row
        for row in conn.execute(
            "SELECT id, note, snapshot_date, superseded_at FROM snapshots WHERE user_id = ?",
            (user_d,),
        )
    }
    assert str(minsk[minsk_later][2]).startswith("2026-10-21")
    assert minsk[minsk_later][3] is None
    assert minsk[minsk_later][1] == "04:00-minsk"
    assert str(minsk[minsk_early][2]).startswith("2026-10-21")
    assert str(minsk[minsk_early][3]).startswith("2026-10-21 01:00:00")
    assert minsk[minsk_early][1] == "00:30-minsk"
    assert str(minsk[minsk_previous][2]).startswith("2026-10-20")
    assert minsk[minsk_previous][3] is None

    index_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'uq_snapshots_user_date'"
    ).fetchone()[0]
    assert "UNIQUE" in index_sql.upper()
    assert "superseded_at IS NULL" in index_sql

    # A second canonical row for that user and day cannot be inserted.
    extra = uuid.uuid4().hex
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO snapshots (id, user_id, created_at, total_usd, is_confirmed, "
            "snapshot_date, superseded_at) VALUES (?, ?, '2026-10-21 19:00:00', 1, 1, "
            "'2026-10-21', NULL)",
            (extra, user_a),
        )
    # A marked row may still share the date: the history is insertable.
    marked = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO snapshots (id, user_id, created_at, total_usd, is_confirmed, "
        "snapshot_date, superseded_at) VALUES (?, ?, '2026-10-21 08:00:00', 1, 1, "
        "'2026-10-21', '2026-10-21 18:00:00')",
        (marked, user_a),
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM snapshots WHERE user_id = ?", (user_a,)).fetchone()[0] == 5
    conn.close()


def test_migration_downgrade_drops_day_columns_and_keeps_every_row(tmp_path):
    db_path = tmp_path / "down.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    env["FERNET_KEY"] = ""
    env["ENVIRONMENT"] = "development"
    backend = pathlib.Path(__file__).resolve().parents[1]

    subprocess.check_call(
        [sys.executable, "-m", "alembic", "upgrade", "b8c9d0e1f2a3"],
        cwd=backend, env=env,
    )
    user = uuid.uuid4().hex
    first = uuid.uuid4().hex
    second = uuid.uuid4().hex
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO users (id, email, base_currency, subscription, email_verified, settings, "
        "totp_enabled, token_version) VALUES (?, ?, 'USD', 'free', 0, '{}', 0, 0)",
        (user, f"{user}@t.test"),
    )
    conn.execute(
        "INSERT INTO snapshots (id, user_id, created_at, total_usd, is_confirmed, note) "
        "VALUES (?, ?, '2026-10-21 09:00:00', 1, 1, 'first')",
        (first, user),
    )
    conn.execute(
        "INSERT INTO snapshots (id, user_id, created_at, total_usd, is_confirmed, note) "
        "VALUES (?, ?, '2026-10-21 18:00:00', 2, 1, 'second')",
        (second, user),
    )
    conn.commit()
    conn.close()

    subprocess.check_call([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend, env=env)
    conn = sqlite3.connect(db_path)
    marked = conn.execute(
        "SELECT note, superseded_at IS NULL FROM snapshots WHERE user_id = ? ORDER BY created_at",
        (user,),
    ).fetchall()
    assert marked == [("first", 0), ("second", 1)]
    conn.close()

    subprocess.check_call(
        [sys.executable, "-m", "alembic", "downgrade", "b8c9d0e1f2a3"],
        cwd=backend, env=env,
    )
    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
    assert "snapshot_date" not in columns
    assert "superseded_at" not in columns
    assert "updated_at" not in columns
    notes = [row[0] for row in conn.execute(
        "SELECT note FROM snapshots WHERE user_id = ? ORDER BY created_at", (user,)
    )]
    assert notes == ["first", "second"]
    conn.close()

    subprocess.check_call([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend, env=env)
    conn = sqlite3.connect(db_path)
    again = conn.execute(
        "SELECT note, superseded_at IS NULL FROM snapshots WHERE user_id = ? ORDER BY created_at",
        (user,),
    ).fetchall()
    assert again == [("first", 0), ("second", 1)]
    conn.close()
