"""fin_attachments: constraints at the DB level and the Alembic migration (fin-02)."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.exc import IntegrityError

from core.db import SessionLocal, engine
from models.finance import FinAttachment, FinExpense, FinProject
from models.types import EncryptedString
from models.user import User

BACKEND = Path(__file__).resolve().parent.parent
ATTACHMENTS_REVISION = "b8c9d0e1f2a3"
PREVIOUS_REVISION = "a7b8c9d0e1f2"
# Head moved on when snapshots became one-per-day. Attachments stay in the chain.
HEAD_REVISION = "c9d0e1f2a3b4"
SHA_A = "a" * 64
SHA_B = "b" * 64


async def _expense(db) -> tuple[User, FinExpense]:
    user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="x", base_currency="USD")
    db.add(user)
    await db.flush()
    project = FinProject(user_id=user.id, name="Машина", base_currency="USD")
    db.add(project)
    await db.flush()
    expense = FinExpense(
        user_id=user.id, project_id=project.id, amount=Decimal("10"), currency="USD",
        spent_on=date(2026, 10, 1),
    )
    db.add(expense)
    await db.flush()
    return user, expense


def _attachment(user: User, expense: FinExpense, sha: str = SHA_A, **kw) -> FinAttachment:
    base = {
        "user_id": user.id,
        "expense_id": expense.id,
        "storage_key": f"u/{user.id}/{expense.id}/{uuid.uuid4()}.jpg",
        "mime": "image/jpeg",
        "size_bytes": 1234,
        "sha256": sha,
    }
    return FinAttachment(**{**base, **kw})


async def test_attachment_defaults_and_roundtrip(db_ready):
    async with SessionLocal() as db:
        user, expense = await _expense(db)
        row = _attachment(user, expense)
        db.add(row)
        await db.commit()
        row_id = row.id

    async with SessionLocal() as db:
        saved = await db.get(FinAttachment, row_id)
        assert isinstance(saved.id, uuid.UUID)
        assert saved.original_name is None  # optional, display only
        assert saved.created_at is not None
        assert (saved.mime, saved.size_bytes, saved.sha256) == ("image/jpeg", 1234, SHA_A)


def test_original_name_is_an_encrypted_column():
    assert isinstance(FinAttachment.__table__.c.original_name.type, EncryptedString)


async def test_the_same_content_cannot_be_attached_twice_to_one_expense(db_ready):
    async with SessionLocal() as db:
        user, expense = await _expense(db)
        db.add(_attachment(user, expense, SHA_A))
        await db.commit()
        db.add(_attachment(user, expense, SHA_A))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()


async def test_the_same_content_may_sit_on_different_expenses_and_different_content_on_one(db_ready):
    async with SessionLocal() as db:
        user, first = await _expense(db)
        second = FinExpense(
            user_id=user.id, project_id=first.project_id, amount=Decimal("1"), currency="USD",
            spent_on=date(2026, 10, 2),
        )
        db.add(second)
        await db.flush()
        db.add_all([
            _attachment(user, first, SHA_A),
            _attachment(user, first, SHA_B),
            _attachment(user, second, SHA_A),
        ])
        await db.commit()

        assert await db.scalar(select(func.count()).select_from(FinAttachment)) == 3


async def test_deleting_the_expense_or_the_user_cascades_when_the_database_enforces_foreign_keys(db_ready):
    def _enforce(dbapi_connection, connection_record, connection_proxy):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    await engine.dispose()
    event.listen(engine.sync_engine, "checkout", _enforce)
    try:
        async with SessionLocal() as db:
            user, expense = await _expense(db)
            db.add(_attachment(user, expense))
            await db.commit()
            user_id, expense_id = user.id, expense.id

            await db.delete(await db.get(FinExpense, expense_id))
            await db.commit()
            assert await db.scalar(select(func.count()).select_from(FinAttachment)) == 0

            other_user, other_expense = await _expense(db)
            db.add(_attachment(other_user, other_expense))
            await db.commit()
            await db.delete(await db.get(User, other_user.id))
            await db.commit()
            assert await db.scalar(select(func.count()).select_from(FinAttachment)) == 0
            assert user_id  # (the first user stayed; only the rows were checked)

        async with SessionLocal() as db:
            user, _ = await _expense(db)
            db.add(FinAttachment(
                user_id=user.id, expense_id=uuid.uuid4(), storage_key="u/x/y/z.jpg",
                mime="image/png", size_bytes=1, sha256=SHA_A,
            ))
            with pytest.raises(IntegrityError):  # an attachment needs a real expense
                await db.commit()
            await db.rollback()
    finally:
        event.remove(engine.sync_engine, "checkout", _enforce)
        await engine.dispose()


# --- migration --------------------------------------------------------------------------------


def _alembic(db_file: Path, *args: str) -> subprocess.CompletedProcess:
    # DATABASE_URL is the only thing steering the subprocess: only a throw-away file outside backend.
    resolved = db_file.resolve()
    assert resolved.suffix == ".db" and BACKEND not in resolved.parents, resolved
    assert resolved.name not in {"kapital.db", "kapital_test.db"}, resolved
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_file.as_posix()}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=180,
    )


def _inspect(db_file: Path):
    return inspect(create_engine(f"sqlite:///{db_file.as_posix()}"))


def test_alembic_upgrade_head_creates_fin_attachments(tmp_path):
    db_file = tmp_path / "migrated.db"

    up = _alembic(db_file, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    heads = _alembic(db_file, "heads")
    assert HEAD_REVISION in heads.stdout and "(head)" in heads.stdout
    insp = _inspect(db_file)
    assert "fin_attachments" in insp.get_table_names()
    assert {c["name"] for c in insp.get_columns("fin_attachments")} == {
        "id", "user_id", "expense_id", "storage_key", "mime", "size_bytes", "sha256",
        "original_name", "created_at",
    }
    columns = {c["name"]: c for c in insp.get_columns("fin_attachments")}
    assert columns["original_name"]["nullable"] is True
    assert str(columns["original_name"]["type"]).upper() == "TEXT"  # EncryptedString -> Text
    for name in ("user_id", "expense_id", "storage_key", "mime", "size_bytes", "sha256", "created_at"):
        assert columns[name]["nullable"] is False, name
    assert [set(u["column_names"]) for u in insp.get_unique_constraints("fin_attachments")] == [
        {"expense_id", "sha256"}
    ]
    assert ["user_id"] in [i["column_names"] for i in insp.get_indexes("fin_attachments")]
    cascades = {
        fk["constrained_columns"][0]: fk["options"].get("ondelete")
        for fk in insp.get_foreign_keys("fin_attachments")
    }
    assert cascades == {"user_id": "CASCADE", "expense_id": "CASCADE"}


def test_the_attachments_revision_chains_after_finance_projects_and_reverses(tmp_path):
    db_file = tmp_path / "roundtrip.db"
    assert _alembic(db_file, "upgrade", "head").returncode == 0

    history = _alembic(db_file, "history")
    assert f"{PREVIOUS_REVISION} -> {ATTACHMENTS_REVISION}" in history.stdout

    down = _alembic(db_file, "downgrade", PREVIOUS_REVISION)
    assert down.returncode == 0, down.stderr[-2000:]
    tables = set(_inspect(db_file).get_table_names())
    assert "fin_attachments" not in tables and {"fin_expenses", "fin_projects"} <= tables

    again = _alembic(db_file, "upgrade", "head")
    assert again.returncode == 0, again.stderr[-2000:]
    assert "fin_attachments" in _inspect(db_file).get_table_names()


def test_alembic_still_has_a_single_head(tmp_path):
    heads = _alembic(tmp_path / "unused.db", "heads")

    assert len([ln for ln in heads.stdout.splitlines() if "(head)" in ln]) == 1, heads.stdout


def test_the_migrated_schema_matches_the_models(tmp_path):
    """`alembic check`: autogenerate finds nothing to add, drop or change."""
    db_file = tmp_path / "check.db"
    assert _alembic(db_file, "upgrade", "head").returncode == 0

    check = _alembic(db_file, "check")

    assert check.returncode == 0, (check.stdout + check.stderr)[-2000:]
