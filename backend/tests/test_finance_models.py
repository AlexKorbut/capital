"""Finance tables: constraints at the DB level + the Alembic migration."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError

from core.db import SessionLocal
from models.finance import FinExpense, FinProject, FxRateDaily
from models.types import EncryptedString
from models.user import User

BACKEND = Path(__file__).resolve().parent.parent


async def _user(db) -> User:
    user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="x", base_currency="USD")
    db.add(user)
    await db.flush()
    return user


async def _project(db, user: User, name: str = "Машина") -> FinProject:
    project = FinProject(user_id=user.id, name=name, base_currency="USD")
    db.add(project)
    await db.flush()
    return project


def _expense(user: User, project: FinProject, **kw) -> FinExpense:
    base = {
        "user_id": user.id,
        "project_id": project.id,
        "amount": Decimal("10"),
        "currency": "USD",
        "spent_on": date(2026, 10, 1),
    }
    return FinExpense(**{**base, **kw})


async def test_project_defaults_and_roundtrip(db_ready):
    async with SessionLocal() as db:
        user = await _user(db)
        project = await _project(db, user)
        await db.commit()
        project_id = project.id

    async with SessionLocal() as db:
        row = await db.get(FinProject, project_id)
        assert row.status == "active"
        assert row.note is None and row.started_on is None
        assert row.created_at is not None and row.updated_at is not None


async def test_project_names_are_unique_per_user_only(db_ready):
    async with SessionLocal() as db:
        alice, bob = await _user(db), await _user(db)
        await _project(db, alice)
        await _project(db, bob)  # same name, other user: fine
        await db.commit()
        db.add(FinProject(user_id=alice.id, name="Машина", base_currency="EUR"))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_project_status_is_constrained(db_ready):
    async with SessionLocal() as db:
        user = await _user(db)
        db.add(FinProject(user_id=user.id, name="X", base_currency="USD", status="deleted"))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_expense_amount_may_be_negative_but_never_zero(db_ready):
    async with SessionLocal() as db:
        user = await _user(db)
        project = await _project(db, user)
        db.add(_expense(user, project, amount=Decimal("-25.5")))  # refund
        await db.commit()
        db.add(_expense(user, project, amount=Decimal("0")))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_expense_client_id_is_unique_per_user_and_optional(db_ready):
    cid = uuid.uuid4()
    async with SessionLocal() as db:
        alice, bob = await _user(db), await _user(db)
        pa, pb = await _project(db, alice), await _project(db, bob)
        # Many rows without a client_id are fine (NULLs never collide) ...
        db.add_all([_expense(alice, pa), _expense(alice, pa)])
        # ... the same client_id for two different users is fine ...
        db.add_all([_expense(alice, pa, client_id=cid), _expense(bob, pb, client_id=cid)])
        await db.commit()
        # ... but not twice for one user.
        db.add(_expense(alice, pa, client_id=cid))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_expense_money_columns_keep_their_precision(db_ready):
    async with SessionLocal() as db:
        user = await _user(db)
        project = await _project(db, user)
        db.add(
            _expense(
                user,
                project,
                amount=Decimal("1234.567891"),
                currency="EUR",
                base_amount=Decimal("1334.57"),
                fx_rate=Decimal("1.0811000000"),
                fx_date=date(2026, 10, 1),
                fx_source="ecb",
                category="топливо",
                note="полный бак",
            )
        )
        await db.commit()
    async with SessionLocal() as db:
        row = (await db.scalars(select(FinExpense))).one()
        assert row.amount == Decimal("1234.567891")
        assert row.base_amount == Decimal("1334.57")
        assert row.fx_rate == Decimal("1.0811")
        assert (row.fx_date, row.fx_source, row.category) == (date(2026, 10, 1), "ecb", "топливо")


def test_notes_are_encrypted_at_rest():
    assert isinstance(FinProject.__table__.c.note.type, EncryptedString)
    assert isinstance(FinExpense.__table__.c.note.type, EncryptedString)


async def test_daily_rates_are_unique_per_date_and_currency(db_ready):
    async with SessionLocal() as db:
        now = datetime.now(timezone.utc)

        def rate(**kw) -> FxRateDaily:
            base = dict(
                rate_date=date(2026, 10, 1),
                currency="EUR",
                usd_per_unit=Decimal("1.1"),
                source="ecb",
                fetched_at=now,
            )
            return FxRateDaily(**{**base, **kw})

        db.add_all([rate(), rate(currency="GBP"), rate(rate_date=date(2026, 10, 2))])
        await db.commit()
        db.add(rate())
        with pytest.raises(IntegrityError):
            await db.commit()


# --- Alembic ---------------------------------------------------------------------

FINANCE_REVISION = "a7b8c9d0e1f2"
PREVIOUS_HEAD = "f6a7b8c9d0e1"


OWNER_DB_FILES = [BACKEND / name for name in ("kapital.db", "kapital.db-wal", "kapital.db-shm")]


def _owner_db_fingerprint() -> list[tuple[int, int] | None]:
    """(size, mtime) of the owner's database files -- stat only, never opened or read."""
    out: list[tuple[int, int] | None] = []
    for path in OWNER_DB_FILES:
        try:
            info = path.stat()
        except FileNotFoundError:
            out.append(None)
        else:
            out.append((info.st_size, info.st_mtime_ns))
    return out


def _alembic(db_file: Path, *args: str) -> subprocess.CompletedProcess:
    # Migrations run in a subprocess, so DATABASE_URL is the ONLY thing steering them:
    # refuse anything that is not an explicit throw-away file outside the backend folder.
    resolved = db_file.resolve()
    assert resolved.suffix == ".db" and BACKEND not in resolved.parents, resolved
    assert resolved.name not in {"kapital.db", "kapital_test.db"}, resolved
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_file.as_posix()}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _inspect(db_file: Path):
    return inspect(create_engine(f"sqlite:///{db_file.as_posix()}"))


def test_alembic_upgrade_head_creates_the_finance_tables(tmp_path):
    db_file = tmp_path / "migrated.db"

    up = _alembic(db_file, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    heads = _alembic(db_file, "heads")
    assert FINANCE_REVISION in heads.stdout and "(head)" in heads.stdout

    insp = _inspect(db_file)
    assert {"fin_projects", "fin_expenses", "fx_rates_daily"} <= set(insp.get_table_names())

    projects = {c["name"] for c in insp.get_columns("fin_projects")}
    assert projects == {
        "id", "user_id", "name", "note", "base_currency", "status",
        "started_on", "created_at", "updated_at",
    }
    expenses = {c["name"] for c in insp.get_columns("fin_expenses")}
    assert expenses == {
        "id", "user_id", "project_id", "amount", "currency", "spent_on", "category",
        "note", "base_amount", "fx_rate", "fx_date", "fx_source", "client_id",
        "created_at", "updated_at",
    }
    rates = {c["name"] for c in insp.get_columns("fx_rates_daily")}
    assert rates == {"id", "date", "currency", "usd_per_unit", "source", "fetched_at"}

    def unique_sets(table: str) -> list[set[str]]:
        return [set(u["column_names"]) for u in insp.get_unique_constraints(table)] + [
            set(i["column_names"]) for i in insp.get_indexes(table) if i["unique"]
        ]

    assert {"user_id", "name"} in unique_sets("fin_projects")
    assert {"user_id", "client_id"} in unique_sets("fin_expenses")
    assert {"date", "currency"} in unique_sets("fx_rates_daily")
    assert ["project_id", "spent_on"] in [i["column_names"] for i in insp.get_indexes("fin_expenses")]
    assert ["user_id"] in [i["column_names"] for i in insp.get_indexes("fin_projects")]


def test_alembic_finance_migration_chains_after_the_previous_head_and_reverses(tmp_path):
    db_file = tmp_path / "roundtrip.db"
    assert _alembic(db_file, "upgrade", "head").returncode == 0

    down = _alembic(db_file, "downgrade", PREVIOUS_HEAD)
    assert down.returncode == 0, down.stderr[-2000:]
    tables = set(_inspect(db_file).get_table_names())
    assert tables.isdisjoint({"fin_projects", "fin_expenses", "fx_rates_daily"})
    assert "api_tokens" in tables  # only the new revision was undone

    again = _alembic(db_file, "upgrade", "head")
    assert again.returncode == 0, again.stderr[-2000:]
    assert "fin_expenses" in _inspect(db_file).get_table_names()


def test_alembic_has_a_single_head(tmp_path):
    heads = _alembic(tmp_path / "unused.db", "heads")
    lines = [ln for ln in heads.stdout.splitlines() if "(head)" in ln]
    assert len(lines) == 1, heads.stdout


def test_alembic_subprocess_resolves_its_database_from_the_environment_only(tmp_path):
    """alembic/env.py takes the URL from core.config.settings: prove that, with the same
    environment and cwd the migration tests use, settings point at the temp file and not
    at whatever backend/.env or the default (./kapital.db) would say."""
    db_file = tmp_path / "only_this.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_file.as_posix()}"}

    out = subprocess.run(
        [sys.executable, "-c", "from core.config import settings; print(settings.database_url)"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60,
    )

    assert out.returncode == 0, out.stderr[-1000:]
    assert out.stdout.strip() == f"sqlite+aiosqlite:///{db_file.as_posix()}"


def test_migration_runs_never_touch_the_owners_database(tmp_path):
    before = _owner_db_fingerprint()
    db_file = tmp_path / "scratch.db"

    assert _alembic(db_file, "upgrade", "head").returncode == 0
    assert _alembic(db_file, "downgrade", PREVIOUS_HEAD).returncode == 0
    assert _alembic(db_file, "upgrade", "head").returncode == 0

    assert db_file.exists() and "fin_projects" in _inspect(db_file).get_table_names()
    assert _owner_db_fingerprint() == before  # sizes and mtimes of kapital.db* unchanged
