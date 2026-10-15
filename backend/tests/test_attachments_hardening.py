"""fin-02 security-review follow-ups: quotas, integrity on read, the FERNET_KEY requirement, an
encrypted original_name, upload concurrency, read limits, hang-ups and slow bodies, response headers."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event, text
from sqlalchemy.dialects import postgresql

from core import quotas
from core.config import settings
from core.db import engine
from services import attachments as attachments_service
from services import upload_stream
from tests.attachment_client import (
    another_expense,
    app_logs,
    files_under,
    jpeg,
    png,
    rows,
    upload,
    upload_ok,
    user_with_expense,
    webp,
)
from tests.finance_client import API, FIN, add_expense, add_ok, client, days_from_today, make_project, register

BOUNDARY = "hardeningboundary"
CORRUPTED_TEXT = "вложение повреждено или ключ не тот"
NOT_CONFIGURED_TEXT = "хранилище вложений не настроено (FERNET_KEY)"
MIB = 1024 * 1024


def _multipart_headers() -> dict[str, str]:
    return {"content-type": f"multipart/form-data; boundary={BOUNDARY}"}


def _part_head(filename: str = "r.jpg") -> bytes:
    return (
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode()


CLOSE = f"\r\n--{BOUNDARY}--\r\n".encode()


def _new_key() -> str:
    return Fernet.generate_key().decode()


# --- (2) quotas: attachments per user, bytes per user, expenses per user ----------------------


async def test_a_user_cannot_hold_more_attachments_than_the_count_quota(db_ready, monkeypatch):
    monkeypatch.setattr(quotas, "MAX_USER_ATTACHMENTS", 3)
    async with await client() as c:
        h, first = await user_with_expense(c)
        second = await another_expense(c, h, first["project_id"])
        kept = await upload_ok(c, h, first["id"], jpeg("1"))
        await upload_ok(c, h, first["id"], jpeg("2"))
        await upload_ok(c, h, second["id"], jpeg("3"))

        over = await upload(c, h, second["id"], jpeg("4"))
        repeat = await upload(c, h, first["id"], jpeg("1"))  # adds nothing: never refused
        stranger, stranger_expense = await user_with_expense(c, "Чужая")
        theirs = await upload(c, stranger, stranger_expense["id"], jpeg("x"))
        await c.delete(f"{FIN}/attachments/{kept['id']}", headers=h)
        after_delete = await upload(c, h, second["id"], jpeg("4"))

    assert over.status_code == 409, over.text
    assert "вложен" in over.json()["detail"] and "3" in over.json()["detail"]
    assert repeat.status_code == 200
    assert theirs.status_code == 201  # the quota is per user
    assert after_delete.status_code == 201  # deleting frees room


async def test_a_user_cannot_hold_more_bytes_than_the_size_quota(db_ready, monkeypatch):
    monkeypatch.setattr(quotas, "MAX_USER_ATTACHMENT_BYTES", 2 * MIB)
    async with await client() as c:
        h, first = await user_with_expense(c)
        second = await another_expense(c, h, first["project_id"])
        a = await upload_ok(c, h, first["id"], jpeg("a", MIB))
        b = await upload(c, h, second["id"], jpeg("b", MIB))  # exactly at the quota: allowed

        over = await upload(c, h, second["id"], jpeg("c", 128))
        stranger, stranger_expense = await user_with_expense(c, "Чужая")
        theirs = await upload(c, stranger, stranger_expense["id"], jpeg("a", MIB))
        await c.delete(f"{FIN}/attachments/{a['id']}", headers=h)
        after_delete = await upload(c, h, second["id"], jpeg("c", 128))

    assert b.status_code == 201
    assert over.status_code == 413, over.text
    assert "2 МБ" in over.json()["detail"]
    assert theirs.status_code == 201
    assert after_delete.status_code == 201


async def test_a_refused_upload_leaves_no_row_and_no_file(db_ready, uploads_dir, monkeypatch):
    monkeypatch.setattr(quotas, "MAX_USER_ATTACHMENTS", 1)
    async with await client() as c:
        h, expense = await user_with_expense(c)
        await upload_ok(c, h, expense["id"], jpeg("1"))

        refused = await upload(c, h, expense["id"], jpeg("2"))

    assert refused.status_code == 409
    assert len(await rows()) == 1 and len(files_under(uploads_dir)) == 1


def test_the_real_ceilings_are_the_agreed_numbers():
    assert quotas.MAX_USER_ATTACHMENT_BYTES == 500 * MIB
    assert quotas.MAX_USER_ATTACHMENTS == 1000
    assert quotas.MAX_USER_EXPENSES == 20_000


async def test_the_expense_ceiling_is_per_user_across_projects(db_ready, fake_fx, monkeypatch):
    monkeypatch.setattr(quotas, "MAX_USER_EXPENSES", 3)
    client_id = str(uuid.uuid4())
    day = days_from_today(0)
    async with await client() as c:
        h = await register(c)
        one = await make_project(c, h, "Один", "USD")
        two = await make_project(c, h, "Два", "USD")
        first = await add_ok(c, h, one["id"], "1", "USD", day, client_id=client_id)
        await add_ok(c, h, one["id"], "2", "USD", day)
        await add_ok(c, h, two["id"], "3", "USD", day)

        over = await add_expense(c, h, two["id"], "4", "USD", day)
        replay = await add_expense(c, h, one["id"], "1", "USD", day, client_id=client_id)
        stranger = await register(c)
        other = await make_project(c, stranger, "Чужой", "USD")
        theirs = await add_expense(c, stranger, other["id"], "5", "USD", day)
        await c.delete(f"{FIN}/expenses/{first['id']}", headers=h)
        after_delete = await add_expense(c, h, two["id"], "4", "USD", day)

    assert over.status_code == 409, over.text
    assert "трат" in over.json()["detail"] and "3" in over.json()["detail"]
    assert replay.status_code == 200 and replay.json()["id"] == first["id"]  # a retry is not a new expense
    assert theirs.status_code == 201
    assert after_delete.status_code == 201


def test_the_upload_lock_takes_the_users_row_with_a_lock_that_does_not_block_foreign_key_checks():
    statement = attachments_service.user_lock_statement(uuid.uuid4())

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "FROM users" in sql
    assert "FOR NO KEY UPDATE" in sql  # serialises one user's uploads, never an FK insert


async def test_an_upload_takes_the_users_row_lock_before_it_checks_the_limits_and_inserts(db_ready):
    statements: list[str] = []

    def spy(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    async with await client() as c:
        h, expense = await user_with_expense(c)
        event.listen(engine.sync_engine, "before_cursor_execute", spy)
        try:
            await upload_ok(c, h, expense["id"], jpeg("locked"))
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", spy)

    lock_at = next(i for i, sql in enumerate(statements) if sql.startswith("SELECT users.id FROM users WHERE"))
    count_at = next(i for i, sql in enumerate(statements) if "count(" in sql and "fin_attachments" in sql)
    insert_at = next(i for i, sql in enumerate(statements) if sql.startswith("INSERT INTO fin_attachments"))
    assert lock_at < count_at < insert_at


# --- (3) integrity on read ---------------------------------------------------------------------


def _only_file(uploads_dir):
    [path] = files_under(uploads_dir)
    return path


async def test_an_encrypted_file_read_without_its_key_is_reported_corrupted_not_served(
    db_ready, uploads_dir, fernet_switch, caplog
):
    key = _new_key()
    fernet_switch(key)
    async with await client() as c:
        h, expense = await user_with_expense(c)
        made = await upload_ok(c, h, expense["id"], jpeg("sealed"))
        [row] = await rows()
        fernet_switch("")  # the key is gone

        with caplog.at_level(logging.DEBUG):
            res = await c.get(f"{FIN}/attachments/{made['id']}", headers=h)
        fernet_switch(key)  # the key is back
        healed = await c.get(f"{FIN}/attachments/{made['id']}", headers=h)

    assert res.status_code == 500, res.text
    assert CORRUPTED_TEXT in res.json()["detail"]
    assert b"\xff\xd8" not in res.content[:2]  # no ciphertext went out as a picture
    logged = app_logs(caplog)
    assert "integrity" in logged.lower()
    for leaked in (str(uploads_dir), made["id"], str(row.user_id), str(row.expense_id), row.storage_key):
        assert leaked not in logged, leaked
    assert healed.status_code == 200 and healed.content == jpeg("sealed")


async def test_an_encrypted_file_read_under_another_key_is_reported_corrupted(
    db_ready, uploads_dir, fernet_switch
):
    fernet_switch(_new_key())
    async with await client() as c:
        h, expense = await user_with_expense(c)
        made = await upload_ok(c, h, expense["id"], jpeg("sealed"))
        fernet_switch(_new_key())

        res = await c.get(f"{FIN}/attachments/{made['id']}", headers=h)

    assert res.status_code == 500 and CORRUPTED_TEXT in res.json()["detail"]


async def test_a_file_written_before_encryption_was_enabled_is_still_served(
    db_ready, uploads_dir, fernet_switch
):
    plain = png("old open file", 300)
    async with await client() as c:
        h, expense = await user_with_expense(c)
        made = await upload_ok(c, h, expense["id"], plain, name="a.png", content_type="image/png")
        assert _only_file(uploads_dir).read_bytes() == plain  # written in the clear
        fernet_switch(_new_key())  # encryption is switched on later

        res = await c.get(f"{FIN}/attachments/{made['id']}", headers=h)

    assert res.status_code == 200 and res.content == plain


@pytest.mark.parametrize("damage", ["flipped byte", "truncated", "swapped for another picture", "emptied"])
async def test_a_file_that_no_longer_matches_its_record_is_reported_corrupted(
    db_ready, uploads_dir, damage
):
    original = jpeg("genuine", 400)
    async with await client() as c:
        h, expense = await user_with_expense(c)
        made = await upload_ok(c, h, expense["id"], original)
        path = _only_file(uploads_dir)
        if damage == "flipped byte":
            path.write_bytes(original[:200] + b"\x01" + original[201:])  # same length, other content
        elif damage == "truncated":
            path.write_bytes(original[:100])
        elif damage == "swapped for another picture":
            path.write_bytes(jpeg("impostor", 400))  # same length, other content
        else:
            path.write_bytes(b"")

        res = await c.get(f"{FIN}/attachments/{made['id']}", headers=h)

    assert res.status_code == 500, res.text
    assert CORRUPTED_TEXT in res.json()["detail"]


async def test_an_intact_file_is_served_after_the_check(db_ready, uploads_dir, fernet_on):
    data = jpeg("fine", 1000)
    async with await client() as c:
        h, expense = await user_with_expense(c)
        made = await upload_ok(c, h, expense["id"], data)

        res = await c.get(f"{FIN}/attachments/{made['id']}", headers=h)

    assert res.status_code == 200 and res.content == data


async def test_hashing_runs_off_the_event_loop_thread_on_upload_and_on_read(db_ready, monkeypatch):
    seen: list[int] = []
    real = attachments_service._digest

    def spy(data):
        seen.append(threading.get_ident())
        return real(data)

    monkeypatch.setattr(attachments_service, "_digest", spy)
    loop_thread = threading.get_ident()
    async with await client() as c:
        h, expense = await user_with_expense(c)
        made = await upload_ok(c, h, expense["id"], jpeg("threads"))
        await c.get(f"{FIN}/attachments/{made['id']}", headers=h)

    assert len(seen) == 2  # one hash to record the file, one to verify it
    assert loop_thread not in seen


# --- (4) the FERNET_KEY requirement --------------------------------------------------------------


async def test_in_production_without_a_fernet_key_uploads_are_refused_with_503(
    db_ready, uploads_dir, monkeypatch
):
    monkeypatch.setattr(settings, "environment", "production")
    async with await client() as c:
        h, expense = await user_with_expense(c)
        intruder = await register(c)

        refused = await upload(c, h, expense["id"], jpeg())
        foreign = await upload(c, intruder, expense["id"], jpeg())

    assert refused.status_code == 503, refused.text
    assert refused.json()["detail"] == NOT_CONFIGURED_TEXT
    assert foreign.status_code == 404  # ownership is judged first, as everywhere
    assert await rows() == [] and files_under(uploads_dir) == []


async def test_in_production_with_a_fernet_key_uploads_work(db_ready, fernet_on, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    async with await client() as c:
        h, expense = await user_with_expense(c)

        res = await upload(c, h, expense["id"], jpeg())

    assert res.status_code == 201, res.text


async def test_in_development_without_a_key_uploads_are_allowed(db_ready):
    assert settings.environment == "development" and settings.fernet_key == ""
    async with await client() as c:
        h, expense = await user_with_expense(c)

        res = await upload(c, h, expense["id"], jpeg())

    assert res.status_code == 201


async def test_in_production_without_a_key_existing_files_can_still_be_read_and_deleted(
    db_ready, monkeypatch
):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        made = await upload_ok(c, h, expense["id"], jpeg("legacy"))
        monkeypatch.setattr(settings, "environment", "production")

        read = await c.get(f"{FIN}/attachments/{made['id']}", headers=h)
        gone = await c.delete(f"{FIN}/attachments/{made['id']}", headers=h)

    assert read.status_code == 200 and gone.status_code == 204


def _stub_lifespan_dependencies(monkeypatch):
    import main
    from contextlib import asynccontextmanager

    class Scheduler:
        def add_cron(self, *a, **k): ...
        def add_interval(self, *a, **k): ...
        def start(self): ...
        def shutdown(self): ...

    @asynccontextmanager
    async def checkpointer():
        yield object()

    monkeypatch.setattr(main, "checkpointer_context", checkpointer)
    monkeypatch.setattr(main, "compile_all", lambda _checkpointer: {})
    monkeypatch.setattr(main, "get_scheduler", lambda: Scheduler())
    monkeypatch.setattr(main.app.state, "graphs", {}, raising=False)
    return main


def _fernet_lines(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "FERNET_KEY" in r.getMessage()]


async def test_startup_in_development_without_a_key_warns_once(monkeypatch, caplog):
    main = _stub_lifespan_dependencies(monkeypatch)

    with caplog.at_level(logging.WARNING):
        async with main.lifespan(main.app):
            pass

    [line] = _fernet_lines(caplog)
    assert line.levelno == logging.WARNING and "unencrypted" in line.getMessage().lower()


async def test_startup_with_a_key_says_nothing_about_it(monkeypatch, caplog, fernet_on):
    main = _stub_lifespan_dependencies(monkeypatch)

    with caplog.at_level(logging.WARNING):
        async with main.lifespan(main.app):
            pass

    assert _fernet_lines(caplog) == []


async def test_startup_in_production_without_a_key_says_uploads_are_off(monkeypatch, caplog):
    main = _stub_lifespan_dependencies(monkeypatch)
    monkeypatch.setattr(settings, "environment", "production")

    with caplog.at_level(logging.WARNING):
        async with main.lifespan(main.app):
            pass

    [line] = _fernet_lines(caplog)
    assert "disabled" in line.getMessage().lower()


# --- (5) original_name is encrypted at rest --------------------------------------------------------


async def test_original_name_is_encrypted_in_the_database_and_clear_in_the_api(db_ready, fernet_on):
    name = "секретное-имя-чека.jpg"
    async with await client() as c:
        h, expense = await user_with_expense(c)

        made = await upload_ok(c, h, expense["id"], jpeg(), name=name)
        export = (await c.get(f"{API}/account/export", headers=h)).json()
        async with engine.connect() as conn:
            raw = (await conn.execute(text("SELECT original_name FROM fin_attachments"))).scalar_one()

    assert made["original_name"] == name
    assert export["fin_attachments"][0]["original_name"] == name
    assert raw != name and "секретное" not in raw and Fernet(fernet_on.encode()).decrypt(raw.encode())


async def test_a_missing_original_name_stays_null(db_ready, fernet_on):
    async with await client() as c:
        h, expense = await user_with_expense(c)

        made = await upload_ok(c, h, expense["id"], jpeg(), name="..")
        async with engine.connect() as conn:
            raw = (await conn.execute(text("SELECT original_name FROM fin_attachments"))).scalar_one()

    assert made["original_name"] is None and raw is None


# --- (6) concurrency, and a limit on reads ---------------------------------------------------------


@pytest.fixture
def slots(monkeypatch):
    """Re-size the upload slots for one test (a fresh semaphore each time)."""

    def configure(limit: int, wait: float = 15.0) -> None:
        monkeypatch.setattr(attachments_service, "MAX_CONCURRENT_UPLOADS", limit)
        monkeypatch.setattr(attachments_service, "UPLOAD_SLOT_WAIT_SECONDS", wait)
        monkeypatch.setattr(attachments_service, "_slots", None)

    return configure


class _ReadSpy:
    """Wraps the body reader: counts how many uploads are inside it at once."""

    def __init__(self, monkeypatch, pause: float) -> None:
        self.active = 0
        self.peak = 0
        real = upload_stream.read_single_file

        async def spy(*args, **kwargs):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(pause)
                return await real(*args, **kwargs)
            finally:
                self.active -= 1

        monkeypatch.setattr(upload_stream, "read_single_file", spy)


async def _concurrent_uploads(c, h, expenses: list[dict], count: int):
    # 5 per expense at most: spread the files over the expenses.
    jobs = [upload(c, h, expenses[i % len(expenses)]["id"], jpeg(f"f{i}")) for i in range(count)]
    return await asyncio.gather(*jobs)


async def test_no_more_uploads_than_the_slot_count_are_in_flight_at_once(db_ready, slots, monkeypatch):
    slots(2)
    spy = _ReadSpy(monkeypatch, pause=0.15)
    async with await client() as c:
        h, first = await user_with_expense(c)
        second = await another_expense(c, h, first["project_id"])

        results = await _concurrent_uploads(c, h, [first, second], 6)

    assert [r.status_code for r in results] == [201] * 6
    assert spy.peak == 2


async def test_the_spy_does_see_all_of_them_when_there_are_enough_slots(db_ready, slots, monkeypatch):
    slots(6)  # control for the test above: without the bound the same load runs 6 wide
    spy = _ReadSpy(monkeypatch, pause=0.15)
    async with await client() as c:
        h, first = await user_with_expense(c)
        second = await another_expense(c, h, first["project_id"])

        await _concurrent_uploads(c, h, [first, second], 6)

    assert spy.peak == 6


def test_the_default_is_four_slots():
    assert attachments_service.MAX_CONCURRENT_UPLOADS == 4


async def test_an_upload_that_cannot_get_a_slot_in_time_is_told_to_retry_with_503(
    db_ready, uploads_dir, slots, monkeypatch
):
    slots(1, wait=0.2)
    _ReadSpy(monkeypatch, pause=1.0)
    async with await client() as c:
        h, first = await user_with_expense(c)
        second = await another_expense(c, h, first["project_id"])

        results = await _concurrent_uploads(c, h, [first, second], 2)

    assert sorted(r.status_code for r in results) == [201, 503]
    busy = next(r for r in results if r.status_code == 503)
    assert "занят" in busy.json()["detail"]
    assert len(await rows()) == 1 and len(files_under(uploads_dir)) == 1


async def test_a_slot_is_released_when_the_upload_fails(db_ready, slots):
    slots(1, wait=0.5)
    async with await client() as c:
        h, expense = await user_with_expense(c)
        bad = await upload(c, h, expense["id"], b"not a picture")  # 415 inside the slot

        good = await upload(c, h, expense["id"], jpeg())

    assert bad.status_code == 415 and good.status_code == 201


async def test_reading_attachments_is_limited_to_120_a_minute_per_user(db_ready):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        made = await upload_ok(c, h, expense["id"], jpeg("popular", 64))
        other, other_expense = await user_with_expense(c, "Чужая")
        theirs = await upload_ok(c, other, other_expense["id"], jpeg("theirs", 64))

        statuses = [
            (await c.get(f"{FIN}/attachments/{made['id']}", headers=h)).status_code for _ in range(121)
        ]
        other_user = await c.get(f"{FIN}/attachments/{theirs['id']}", headers=other)

    assert statuses[:120] == [200] * 120
    assert statuses[120] == 429
    assert other_user.status_code == 200  # another user has a bucket of their own


# --- (8) hang-ups, slow bodies, headers -------------------------------------------------------------


async def _raw_asgi_post(app, path: str, headers: dict[str, str], messages: list[dict]) -> list[dict]:
    """Drive the ASGI app by hand: `messages` are what the client sends, then it hangs up."""
    pending = list(messages)
    sent: list[dict] = []

    async def receive():
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("203.0.113.9", 5000),
        "server": ("t", 80),
    }
    await app(scope, receive, send)
    return sent


async def test_a_client_that_hangs_up_mid_upload_is_neither_a_500_nor_a_sentry_event(
    db_ready, uploads_dir, monkeypatch, caplog
):
    import sentry_sdk

    from main import app

    reported: list[object] = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda *a, **k: reported.append(a))
    async with await client() as c:
        h, expense = await user_with_expense(c)

    with caplog.at_level(logging.DEBUG):
        sent = await _raw_asgi_post(
            app,
            f"{FIN}/expenses/{expense['id']}/attachments",
            {**h, **_multipart_headers()},
            [
                {"type": "http.request", "body": _part_head() + jpeg("half"), "more_body": True},
                {"type": "http.disconnect"},
            ],
        )

    [start] = [m for m in sent if m["type"] == "http.response.start"]
    assert start["status"] == 499
    assert reported == []
    assert "Unhandled error" not in caplog.text
    assert await rows() == [] and files_under(uploads_dir) == []


async def test_a_body_that_trickles_in_is_cut_off_after_the_read_deadline(
    db_ready, uploads_dir, monkeypatch
):
    monkeypatch.setattr(upload_stream, "READ_TIMEOUT_SECONDS", 0.3)

    class Trickle:
        async def __aiter__(self):
            yield _part_head()
            yield jpeg("slow")
            await asyncio.sleep(3)  # the rest never comes in time
            yield CLOSE

    async with await client() as c:
        h, expense = await user_with_expense(c)
        started = time.monotonic()

        res = await c.post(
            f"{FIN}/expenses/{expense['id']}/attachments",
            content=Trickle(),
            headers={**h, **_multipart_headers()},
        )
        took = time.monotonic() - started

    assert res.status_code == 408, res.text
    assert took < 2.5
    assert await rows() == [] and files_under(uploads_dir) == []


def test_the_default_read_deadline_is_two_minutes():
    assert upload_stream.READ_TIMEOUT_SECONDS == 120


async def test_the_picture_is_served_with_no_store_and_a_locked_down_csp(db_ready):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        made = await upload_ok(c, h, expense["id"], jpeg("headers"))

        res = await c.get(f"{FIN}/attachments/{made['id']}", headers=h)

    assert res.headers["cache-control"] == "private, no-store"
    assert res.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["content-disposition"] == 'inline; filename="receipt.jpg"'


async def test_a_well_formed_webp_is_accepted_and_a_lying_riff_header_is_not(db_ready, uploads_dir):
    lying = b"RIFF" + (10_000).to_bytes(4, "little") + b"WEBPVP8 " + b"\x00" * 64
    async with await client() as c:
        h, expense = await user_with_expense(c)

        ok = await upload(c, h, expense["id"], webp("fine"), name="a.webp", content_type="image/webp")
        bad = await upload(c, h, expense["id"], lying, name="b.webp", content_type="image/webp")

    assert ok.status_code == 201 and ok.json()["mime"] == "image/webp"
    assert bad.status_code == 415
    assert len(await rows()) == 1 and len(files_under(uploads_dir)) == 1


async def test_deleting_the_account_removes_the_whole_user_folder_including_strays(db_ready, uploads_dir):
    async with await client() as c:
        email = f"{uuid.uuid4().hex}@example.com"
        res = await c.post(f"{API}/auth/register", json={"email": email, "password": "supersecret1"})
        h = {"Authorization": f"Bearer {res.json()['tokens']['access_token']}"}
        project = await make_project(c, h, "Папка", "USD")
        expense = await add_ok(c, h, project["id"], "1", "USD", days_from_today(0))
        await upload_ok(c, h, expense["id"], jpeg("one"))
        [row] = await rows()
        folder = uploads_dir / "u" / str(row.user_id) / str(row.expense_id)
        (folder / ".half-written.jpg.cafe.tmp").write_bytes(b"left by a crash")
        (folder / "stray.bin").write_bytes(b"unknown")
        elsewhere = uploads_dir / "u" / str(uuid.uuid4())
        elsewhere.mkdir(parents=True)
        (elsewhere / "someone-else.bin").write_bytes(b"not this user")

        gone = await c.request("DELETE", f"{API}/account", json={"confirm_email": email}, headers=h)

    assert gone.status_code == 204, gone.text
    assert not (uploads_dir / "u" / str(row.user_id)).exists()
    assert (elsewhere / "someone-else.bin").exists()  # another user's folder is untouched
