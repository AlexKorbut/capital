"""Receipt files follow the life of their expense, project and account (fin-02)."""
from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import event, func, select

from core.db import SessionLocal, engine
from core.storage import LocalFsStorage, get_storage
from models.finance import FinAttachment, FinExpense, FinProject
from services import finance as finance_service
from tests.attachment_client import (
    app_logs,
    files_under,
    jpeg,
    png,
    rows,
    upload_ok,
    user_with_expense,
)
from tests.finance_client import API, FIN, add_ok, client, days_from_today, make_project, register


async def _with_email(c) -> tuple[dict[str, str], uuid.UUID, str]:
    email = f"{uuid.uuid4().hex}@example.com"
    res = await c.post(f"{API}/auth/register", json={"email": email, "password": "supersecret1"})
    assert res.status_code == 201, res.text
    headers = {"Authorization": f"Bearer {res.json()['tokens']['access_token']}"}
    me = (await c.get(f"{API}/auth/me", headers=headers)).json()
    return headers, uuid.UUID(me["id"]), email


async def _expense(c, headers, project_id: str, spent_on: str | None = None) -> dict:
    return await add_ok(c, headers, project_id, "10", "USD", spent_on or days_from_today(0))


async def _count(model) -> int:
    async with SessionLocal() as db:
        return await db.scalar(select(func.count()).select_from(model))


class StubbornDisk(LocalFsStorage):
    """A disk that never lets a file go; the error text carries a user path on purpose."""

    def delete(self, key: str) -> None:
        raise PermissionError(f"[WinError 5] denied: C:\\private\\folder\\{key}")

    def delete_prefix(self, prefix: str) -> None:  # account deletion removes the whole folder
        raise PermissionError(f"[WinError 5] denied: C:\\private\\folder\\{prefix}")


def _stubborn(app, uploads_dir):
    app.dependency_overrides[get_storage] = lambda: StubbornDisk(uploads_dir)


# --- expense / project -------------------------------------------------------------------------


async def test_deleting_an_expense_removes_its_attachments_and_their_files(db_ready, uploads_dir):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        kept = await _expense(c, h, expense["project_id"])
        await upload_ok(c, h, expense["id"], jpeg("a"))
        await upload_ok(c, h, expense["id"], png("b"))
        survivor = await upload_ok(c, h, kept["id"], jpeg("c"))

        res = await c.delete(f"{FIN}/expenses/{expense['id']}", headers=h)
        still = await c.get(f"{FIN}/attachments/{survivor['id']}", headers=h)

    assert res.status_code == 204
    assert [str(r.id) for r in await rows()] == [survivor["id"]]
    assert len(files_under(uploads_dir)) == 1 and still.status_code == 200


async def test_deleting_a_project_removes_the_files_of_all_its_expenses_only(db_ready, uploads_dir):
    async with await client() as c:
        h, expense = await user_with_expense(c, "Удаляемый")
        second = await _expense(c, h, expense["project_id"])
        other_project = await make_project(c, h, "Остаётся", "USD")
        other_expense = await _expense(c, h, other_project["id"])
        await upload_ok(c, h, expense["id"], jpeg("a"))
        await upload_ok(c, h, second["id"], jpeg("b"))
        await upload_ok(c, h, second["id"], png("c"))
        survivor = await upload_ok(c, h, other_expense["id"], jpeg("d"))

        res = await c.delete(f"{FIN}/projects/{expense['project_id']}", headers=h)

    assert res.status_code == 204
    assert [str(r.id) for r in await rows()] == [survivor["id"]]
    assert len(files_under(uploads_dir)) == 1


@pytest.mark.parametrize("what", ["expense", "project"])
async def test_a_file_that_will_not_go_does_not_fail_the_deletion_and_logs_no_paths(
    db_ready, uploads_dir, caplog, what
):
    from main import app

    async with await client() as c:
        h, expense = await user_with_expense(c)
        made = await upload_ok(c, h, expense["id"], jpeg("stuck"))
        _stubborn(app, uploads_dir)
        try:
            with caplog.at_level(logging.DEBUG):
                url = f"{FIN}/expenses/{expense['id']}" if what == "expense" else f"{FIN}/projects/{expense['project_id']}"
                res = await c.delete(url, headers=h)
        finally:
            app.dependency_overrides.pop(get_storage, None)

    assert res.status_code == 204, res.text
    assert await rows() == [] and await _count(FinExpense) == 0
    logged = app_logs(caplog)
    assert "PermissionError" in logged
    for leaked in ("private", "WinError", made["id"], str(uploads_dir), expense["id"]):
        assert leaked not in logged, leaked


@pytest.mark.parametrize("what", ["expense", "project"])
async def test_files_are_removed_only_after_the_commit_succeeded(db_ready, uploads_dir, monkeypatch, what):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        await upload_ok(c, h, expense["id"], jpeg("precious"))

    async with SessionLocal() as db:
        target = await db.get(FinExpense if what == "expense" else FinProject,
                              uuid.UUID(expense["id"] if what == "expense" else expense["project_id"]))

        async def failing_commit():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", failing_commit)
        delete = finance_service.delete_expense if what == "expense" else finance_service.delete_project
        with pytest.raises(RuntimeError):
            await delete(db, target, LocalFsStorage(uploads_dir))
        await db.rollback()

    assert len(files_under(uploads_dir)) == 1  # the picture survived the failed delete
    assert len(await rows()) == 1


# --- account ---------------------------------------------------------------------------------


async def test_deleting_the_account_removes_its_files_and_rows_and_nobody_elses(db_ready, uploads_dir):
    async with await client() as c:
        alice, alice_id, alice_email = await _with_email(c)
        bob, bob_id, _ = await _with_email(c)
        a_project = await make_project(c, alice, "A", "USD")
        b_project = await make_project(c, bob, "B", "USD")
        a1, a2 = await _expense(c, alice, a_project["id"]), await _expense(c, alice, a_project["id"])
        b1 = await _expense(c, bob, b_project["id"])
        await upload_ok(c, alice, a1["id"], jpeg("a1"))
        await upload_ok(c, alice, a2["id"], jpeg("a2"))
        survivor = await upload_ok(c, bob, b1["id"], jpeg("b1"))

        res = await c.request("DELETE", f"{API}/account", json={"confirm_email": alice_email}, headers=alice)
        bobs = await c.get(f"{FIN}/attachments/{survivor['id']}", headers=bob)

    assert res.status_code == 204, res.text
    assert [str(r.user_id) for r in await rows()] == [str(bob_id)]
    assert len(files_under(uploads_dir)) == 1 and bobs.status_code == 200
    assert not (uploads_dir / "u" / str(alice_id)).exists()  # no empty folders left behind


async def test_a_wrong_confirmation_keeps_the_files(db_ready, uploads_dir):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        await upload_ok(c, h, expense["id"], jpeg())

        res = await c.request("DELETE", f"{API}/account", json={"confirm_email": "nope@example.com"}, headers=h)

    assert res.status_code == 400
    assert len(await rows()) == 1 and len(files_under(uploads_dir)) == 1


async def test_account_deletion_works_when_the_database_enforces_foreign_keys(db_ready, uploads_dir):
    """Postgres refuses to drop an expense that still has attachments: they must go first."""

    def _enforce(dbapi_connection, connection_record, connection_proxy):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with await client() as c:
        alice, _, alice_email = await _with_email(c)
        project = await make_project(c, alice, "Машина", "USD")
        expense = await _expense(c, alice, project["id"])
        await upload_ok(c, alice, expense["id"], jpeg("fk"))

        await engine.dispose()
        event.listen(engine.sync_engine, "checkout", _enforce)
        try:
            res = await c.request("DELETE", f"{API}/account", json={"confirm_email": alice_email}, headers=alice)
        finally:
            event.remove(engine.sync_engine, "checkout", _enforce)
            await engine.dispose()

    assert res.status_code == 204, res.text
    assert await rows() == [] and files_under(uploads_dir) == []


async def test_a_file_that_will_not_go_does_not_fail_the_account_deletion(db_ready, uploads_dir, caplog):
    from main import app

    async with await client() as c:
        alice, _, alice_email = await _with_email(c)
        project = await make_project(c, alice, "A", "USD")
        expense = await _expense(c, alice, project["id"])
        await upload_ok(c, alice, expense["id"], jpeg("stuck"))
        _stubborn(app, uploads_dir)
        try:
            with caplog.at_level(logging.DEBUG):
                res = await c.request(
                    "DELETE", f"{API}/account", json={"confirm_email": alice_email}, headers=alice
                )
        finally:
            app.dependency_overrides.pop(get_storage, None)

    assert res.status_code == 204, res.text
    assert await rows() == []
    logged = app_logs(caplog)
    assert "PermissionError" in logged and "private" not in logged and alice_email not in logged


# --- export -------------------------------------------------------------------------------------


async def test_export_has_attachment_metadata_without_bytes_or_storage_keys(db_ready, uploads_dir):
    async with await client() as c:
        alice, alice_id, _ = await _with_email(c)
        bob, _, _ = await _with_email(c)
        a_project = await make_project(c, alice, "A", "USD")
        a_expense = await _expense(c, alice, a_project["id"])
        b_project = await make_project(c, bob, "B", "USD")
        b_expense = await _expense(c, bob, b_project["id"])
        mine = await upload_ok(c, alice, a_expense["id"], jpeg("RECEIPT-PIXELS", 400), name="мой чек.jpg")
        theirs = await upload_ok(c, bob, b_expense["id"], png("FOREIGN-PIXELS"))

        res = await c.get(f"{API}/account/export", headers=alice)

    assert res.status_code == 200, res.text
    dump = res.json()
    [item] = dump["fin_attachments"]
    assert item["id"] == mine["id"] and item["expense_id"] == a_expense["id"]
    assert item["mime"] == "image/jpeg" and item["size_bytes"] == 400
    assert item["original_name"] == "мой чек.jpg" and len(item["sha256"]) == 64
    assert item["user_id"] == str(alice_id) and item["created_at"]
    assert set(item) == {
        "id", "user_id", "expense_id", "mime", "size_bytes", "sha256", "original_name", "created_at",
    }
    assert "storage_key" not in res.text and str(uploads_dir) not in res.text
    assert "RECEIPT-PIXELS" not in res.text  # no bytes (nor base64 of them: it is metadata only)
    assert theirs["id"] not in res.text and "FOREIGN-PIXELS" not in res.text


async def test_export_without_attachments_has_an_empty_list(db_ready):
    async with await client() as c:
        h = await register(c)

        dump = (await c.get(f"{API}/account/export", headers=h)).json()

    assert dump["fin_attachments"] == []


async def test_attachment_rows_count_matches_files_after_mixed_operations(db_ready, uploads_dir):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        a = await upload_ok(c, h, expense["id"], jpeg("1"))
        await upload_ok(c, h, expense["id"], jpeg("2"))
        await c.delete(f"{FIN}/attachments/{a['id']}", headers=h)
        await upload_ok(c, h, expense["id"], jpeg("3"))

    assert await _count(FinAttachment) == len(files_under(uploads_dir)) == 2
