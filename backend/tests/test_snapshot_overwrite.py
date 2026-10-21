"""One snapshot per UTC day, and an explicit overwrite of that day.

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
from core.db import SessionLocal, engine
from models.advice import AdviceItem, AdviceSession
from models.asset import Asset
from models.scenario import Scenario
from models.snapshot import Snapshot
from models.user import User
from services import portfolio_read, returns as returns_service
from services.persistence import (
    SnapshotDateConflict,
    find_snapshot,
    save_portfolio,
    utc_today,
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
        assert snap.snapshot_date == utc_today()
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
    assert caught.value.snapshot_date == utc_today()
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
    yesterday = utc_today() - timedelta(days=1)

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
        assert mine.snapshot_date == utc_today()
        assert mine.total_usd == Decimal("200")


async def test_overwrite_replaces_assets_recalculates_and_keeps_links(db_ready):
    uid = await _user()
    yesterday = utc_today() - timedelta(days=1)
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
        assert snap.snapshot_date == utc_today()

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
        assert detail["snapshot_date"] == utc_today().isoformat()
        assert detail["snapshot_id"] == snapshot_id
        assert detail["message"].startswith(f"Снимок за {utc_today().strftime('%d.%m.%Y')}")
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
            for offset, day in enumerate((date(2020, 3, 1), date(2020, 3, 2), utc_today())):
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
                snapshot_date=utc_today(),
                superseded_at=_at(utc_today(), 9),
                created_at=_at(utc_today(), 9),
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
