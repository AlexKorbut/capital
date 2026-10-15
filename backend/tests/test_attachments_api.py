"""Receipt photos on expenses (fin-02): upload, read, delete, limits and isolation."""
from __future__ import annotations

import asyncio
import logging
import re

import pytest
from cryptography.fernet import Fernet

from core.config import settings
from tests.attachment_client import (
    GIF,
    HEIC,
    MAX_BYTES,
    PDF,
    UNSUPPORTED_TEXT,
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
from tests.finance_client import API, FIN, add_ok, client, days_from_today, make_project, register

BOUNDARY = "kapitalboundary123"
BRIEF_KEYS = {"id", "mime", "size_bytes", "created_at"}


def _multipart_headers(length: int | None = None) -> dict[str, str]:
    headers = {"content-type": f"multipart/form-data; boundary={BOUNDARY}"}
    if length is not None:
        headers["content-length"] = str(length)
    return headers


def _part_head(name: str = "file", filename: str | None = "r.jpg") -> bytes:
    disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
    return f"--{BOUNDARY}\r\nContent-Disposition: {disposition}\r\n\r\n".encode()


CLOSE = f"\r\n--{BOUNDARY}--\r\n".encode()


class CountingBody:
    """A request body that remembers how many chunks the server actually pulled."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.pulled = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.pulled += 1
            yield chunk


# --- happy path -------------------------------------------------------------------------


async def test_uploading_a_jpeg_answers_201_with_the_documented_fields(db_ready, uploads_dir):
    data = jpeg("first", 300)
    async with await client() as c:
        h, expense = await user_with_expense(c)

        res = await upload(c, h, expense["id"], data, name="чек из магазина.jpg")

    assert res.status_code == 201, res.text
    body = res.json()
    assert set(body) == {"id", "mime", "size_bytes", "created_at", "original_name"}
    assert body["mime"] == "image/jpeg" and body["size_bytes"] == 300
    assert body["original_name"] == "чек из магазина.jpg"
    assert body["created_at"]
    [row] = await rows(expense["id"])
    assert str(row.id) == body["id"] and row.mime == "image/jpeg" and row.size_bytes == 300
    assert len(row.sha256) == 64


async def test_png_and_webp_are_accepted_too(db_ready):
    async with await client() as c:
        h, expense = await user_with_expense(c)

        a = await upload_ok(c, h, expense["id"], png(), name="a.png", content_type="image/png")
        b = await upload_ok(c, h, expense["id"], webp(), name="a.webp", content_type="image/webp")

    assert (a["mime"], b["mime"]) == ("image/png", "image/webp")


async def test_the_type_comes_from_the_bytes_not_from_the_name_or_the_declared_type(db_ready):
    async with await client() as c:
        h, expense = await user_with_expense(c)

        made = await upload_ok(c, h, expense["id"], png(), name="photo.jpg", content_type="image/jpeg")
        res = await c.get(f"{FIN}/attachments/{made['id']}", headers=h)

    assert made["mime"] == "image/png"
    assert res.headers["content-type"] == "image/png"
    assert 'filename="receipt.png"' in res.headers["content-disposition"]


async def test_extra_form_fields_are_ignored(db_ready):
    async with await client() as c:
        h, expense = await user_with_expense(c)

        res = await c.post(
            f"{FIN}/expenses/{expense['id']}/attachments",
            files={"file": ("r.jpg", jpeg(), "image/jpeg")},
            data={"note": "hello"},
            headers=h,
        )

    assert res.status_code == 201, res.text


# --- the storage key is built by the server only ------------------------------------------


@pytest.mark.parametrize(
    ("client_name", "shown"),
    [
        ("../../evil.jpg", "evil.jpg"),
        ("..\\..\\evil.jpg", "evil.jpg"),
        ("C:\\Users\\me\\Receipt.JPG", "Receipt.JPG"),
        ("/etc/passwd.jpg", "passwd.jpg"),
        ("a/b/c/d.jpg", "d.jpg"),
    ],
)
async def test_a_path_in_the_client_file_name_never_reaches_the_storage_path(
    db_ready, uploads_dir, client_name, shown
):
    async with await client() as c:
        h, expense = await user_with_expense(c)

        made = await upload_ok(c, h, expense["id"], jpeg(), name=client_name)

    [row] = await rows(expense["id"])
    pattern = rf"^u/{row.user_id}/{expense['id']}/{made['id']}\.jpg$"
    assert re.match(pattern, row.storage_key), row.storage_key
    assert files_under(uploads_dir) == [uploads_dir / row.storage_key]
    assert made["original_name"] == shown
    assert not (uploads_dir.parent / "evil.jpg").exists()
    assert not (uploads_dir.parent.parent / "evil.jpg").exists()


async def _raw_upload(c, headers, expense_id: str, data: bytes, filename: str):
    """Multipart body built by hand: the file name reaches the server byte for byte."""
    raw = _part_head(filename=filename) + data + CLOSE
    return await c.post(
        f"{FIN}/expenses/{expense_id}/attachments", content=raw, headers={**headers, **_multipart_headers()}
    )


async def test_the_shown_name_is_cleaned_and_shortened(db_ready):
    async with await client() as c:
        h, expense = await user_with_expense(c)

        long_one = await _raw_upload(c, h, expense["id"], jpeg("1"), ("я" * 300) + ".jpg")
        tricky = await _raw_upload(c, h, expense["id"], jpeg("2"), "a\x00b\x07c.jpg")
        dots = await _raw_upload(c, h, expense["id"], jpeg("3"), "..")
        padded = await _raw_upload(c, h, expense["id"], jpeg("4"), "  Receipt   .jpg  ")

    assert len(long_one.json()["original_name"]) == 200
    assert tricky.json()["original_name"] == "abc.jpg"
    assert dots.json()["original_name"] is None
    assert padded.json()["original_name"] == "Receipt   .jpg"


# --- type by signature --------------------------------------------------------------------


async def test_anything_that_is_not_jpeg_png_or_webp_is_415_and_leaves_nothing_behind(db_ready, uploads_dir):
    forged = [
        ("text renamed to jpg", b"this is not a picture at all"),
        ("html", b"<html><script>alert(1)</script></html>"),
        ("heic", HEIC),
        ("gif", GIF),
        ("pdf", PDF),
        ("empty", b""),
        ("truncated png", b"\x89PNG\r\n\x1a"),
        ("riff wave", b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 32),
    ]
    async with await client() as c:
        h, expense = await user_with_expense(c)

        for label, data in forged:
            res = await upload(c, h, expense["id"], data, name="receipt.jpg", content_type="image/jpeg")
            assert res.status_code == 415, (label, res.text)
            assert UNSUPPORTED_TEXT in res.json()["detail"], label

    assert await rows() == [] and files_under(uploads_dir) == []


# --- size limit -----------------------------------------------------------------------------


async def test_exactly_ten_megabytes_passes_and_one_byte_more_is_413(db_ready, uploads_dir):
    async with await client() as c:
        h, expense = await user_with_expense(c)

        ok = await upload(c, h, expense["id"], jpeg("edge", MAX_BYTES))
        over = await upload(c, h, expense["id"], jpeg("edge2", MAX_BYTES + 1))

    assert ok.status_code == 201, ok.text
    assert ok.json()["size_bytes"] == MAX_BYTES
    assert over.status_code == 413, over.text
    assert len(await rows()) == 1 and len(files_under(uploads_dir)) == 1


async def test_a_declared_content_length_over_the_limit_is_refused_before_reading(db_ready, uploads_dir):
    body = CountingBody([_part_head(), jpeg(), CLOSE])
    async with await client() as c:
        h, expense = await user_with_expense(c)

        res = await c.post(
            f"{FIN}/expenses/{expense['id']}/attachments",
            content=body,
            headers={**h, **_multipart_headers(length=2 * MAX_BYTES)},
        )

    assert res.status_code == 413, res.text
    assert body.pulled == 0  # not a single chunk of the body was read
    assert await rows() == [] and files_under(uploads_dir) == []


async def test_a_stream_without_content_length_is_cut_off_as_soon_as_it_is_too_big(db_ready, uploads_dir):
    megabyte = b"\x01" * (1024 * 1024)
    chunks = [_part_head(), jpeg("s", 64)] + [megabyte] * 30 + [CLOSE]
    body = CountingBody(chunks)
    async with await client() as c:
        h, expense = await user_with_expense(c)

        res = await c.post(
            f"{FIN}/expenses/{expense['id']}/attachments",
            content=body,
            headers={**h, **_multipart_headers()},
        )

    assert res.status_code == 413, res.text
    assert body.pulled < len(chunks) - 10  # the remaining ~20 MB were never read
    assert await rows() == [] and files_under(uploads_dir) == []


async def test_a_huge_non_file_field_cannot_smuggle_a_big_body_through(db_ready):
    megabyte = b"a" * (1024 * 1024)
    chunks = [_part_head("note", None)] + [megabyte] * 12 + [CLOSE]
    body = CountingBody(chunks)
    async with await client() as c:
        h, expense = await user_with_expense(c)

        res = await c.post(
            f"{FIN}/expenses/{expense['id']}/attachments",
            content=body,
            headers={**h, **_multipart_headers()},
        )

    assert res.status_code == 413, res.text
    assert body.pulled < len(chunks)


# --- malformed requests ---------------------------------------------------------------------


async def test_a_request_without_the_file_field_is_422(db_ready):
    async with await client() as c:
        h, expense = await user_with_expense(c)

        no_file = await c.post(
            f"{FIN}/expenses/{expense['id']}/attachments",
            files={"other": ("r.jpg", jpeg(), "image/jpeg")},
            headers=h,
        )
        json_body = await c.post(
            f"{FIN}/expenses/{expense['id']}/attachments", json={"file": "x"}, headers=h
        )
        empty_form = await c.post(
            f"{FIN}/expenses/{expense['id']}/attachments",
            content=CLOSE.lstrip(b"\r\n"),
            headers={**h, **_multipart_headers()},
        )

    assert no_file.status_code == json_body.status_code == empty_form.status_code == 422


async def test_two_file_fields_are_refused(db_ready, uploads_dir):
    raw = _part_head() + jpeg("1") + b"\r\n" + _part_head() + jpeg("2") + CLOSE
    async with await client() as c:
        h, expense = await user_with_expense(c)

        res = await c.post(
            f"{FIN}/expenses/{expense['id']}/attachments", content=raw, headers={**h, **_multipart_headers()}
        )

    assert res.status_code == 422, res.text
    assert await rows() == [] and files_under(uploads_dir) == []


async def test_a_body_that_ends_before_the_closing_boundary_is_refused(db_ready, uploads_dir):
    raw = _part_head() + jpeg("cut")  # no closing boundary
    async with await client() as c:
        h, expense = await user_with_expense(c)

        res = await c.post(
            f"{FIN}/expenses/{expense['id']}/attachments", content=raw, headers={**h, **_multipart_headers()}
        )

    assert res.status_code == 422, res.text
    assert await rows() == [] and files_under(uploads_dir) == []


async def test_a_flood_of_tiny_parts_is_refused(db_ready):
    raw = b"".join(_part_head(f"f{i}", None) + b"x" for i in range(50)) + CLOSE
    async with await client() as c:
        h, expense = await user_with_expense(c)

        res = await c.post(
            f"{FIN}/expenses/{expense['id']}/attachments", content=raw, headers={**h, **_multipart_headers()}
        )

    assert res.status_code == 422, res.text


async def test_a_malformed_expense_id_is_422(db_ready):
    async with await client() as c:
        h = await register(c)

        res = await upload(c, h, "not-a-uuid", jpeg())

    assert res.status_code == 422


# --- duplicates and the per-expense cap -------------------------------------------------------


async def test_the_same_file_on_the_same_expense_returns_the_first_attachment(db_ready, uploads_dir):
    data = jpeg("same")
    async with await client() as c:
        h, expense = await user_with_expense(c)
        first = await upload(c, h, expense["id"], data, name="one.jpg")

        again = await upload(c, h, expense["id"], data, name="two.jpg")

    assert first.status_code == 201 and again.status_code == 200
    assert again.json() == first.json()  # including the first name
    assert len(await rows(expense["id"])) == 1 and len(files_under(uploads_dir)) == 1


async def test_concurrent_uploads_of_one_file_end_up_as_a_single_attachment(db_ready, uploads_dir):
    data = jpeg("race")
    async with await client() as c:
        h, expense = await user_with_expense(c)

        results = await asyncio.gather(*[upload(c, h, expense["id"], data) for _ in range(4)])

    assert all(r.status_code in (200, 201) for r in results), [r.text for r in results]
    assert [r.status_code for r in results].count(201) == 1
    assert len({r.json()["id"] for r in results}) == 1
    assert len(await rows(expense["id"])) == 1 and len(files_under(uploads_dir)) == 1


async def test_the_same_file_on_another_expense_is_a_separate_attachment(db_ready, uploads_dir):
    data = jpeg("shared")
    async with await client() as c:
        h, first_expense = await user_with_expense(c)
        second_expense = await another_expense(c, h, first_expense["project_id"])

        a = await upload(c, h, first_expense["id"], data)
        b = await upload(c, h, second_expense["id"], data)

    assert (a.status_code, b.status_code) == (201, 201)
    assert a.json()["id"] != b.json()["id"]
    assert len(files_under(uploads_dir)) == 2


async def test_five_attachments_fit_and_the_sixth_is_409(db_ready, uploads_dir):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        for i in range(5):
            await upload_ok(c, h, expense["id"], jpeg(f"n{i}"))

        sixth = await upload(c, h, expense["id"], jpeg("n5"))
        repeat = await upload(c, h, expense["id"], jpeg("n0"))  # not a new file: nothing to refuse

    assert sixth.status_code == 409, sixth.text
    assert repeat.status_code == 200
    assert len(await rows(expense["id"])) == 5 and len(files_under(uploads_dir)) == 5


async def test_the_cap_is_per_expense(db_ready):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        other = await another_expense(c, h, expense["project_id"])
        for i in range(5):
            await upload_ok(c, h, expense["id"], jpeg(f"n{i}"))

        res = await upload(c, h, other["id"], jpeg("fresh"))

    assert res.status_code == 201


# --- rate limit ---------------------------------------------------------------------------------


async def test_uploads_are_limited_to_twenty_a_minute_per_user(db_ready):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        stranger, stranger_expense = await user_with_expense(c, "Чужая")
        statuses = [(await upload(c, h, expense["id"], jpeg())).status_code for _ in range(21)]

        other_user = await upload(c, stranger, stranger_expense["id"], jpeg())

    assert statuses[:20] == [201] + [200] * 19
    assert statuses[20] == 429
    assert other_user.status_code == 201  # another user has a bucket of their own


# --- reading ------------------------------------------------------------------------------------


async def test_get_returns_the_bytes_with_safe_headers_and_no_client_name(db_ready):
    data = png("pic", 500)
    async with await client() as c:
        h, expense = await user_with_expense(c)
        made = await upload_ok(c, h, expense["id"], data, name="мой секретный чек.png", content_type="image/png")

        res = await c.get(f"{FIN}/attachments/{made['id']}", headers=h)

    assert res.status_code == 200
    assert res.content == data
    assert res.headers["content-type"] == "image/png"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["cache-control"] == "private, no-store"
    assert res.headers["content-disposition"] == 'inline; filename="receipt.png"'
    assert "секретный" not in str(res.headers)


async def test_get_without_credentials_is_401_and_unknown_ids_are_404(db_ready):
    import uuid

    async with await client() as c:
        h, expense = await user_with_expense(c)
        made = await upload_ok(c, h, expense["id"], jpeg())

        anonymous = await c.get(f"{FIN}/attachments/{made['id']}")
        unknown = await c.get(f"{FIN}/attachments/{uuid.uuid4()}", headers=h)
        malformed = await c.get(f"{FIN}/attachments/nope", headers=h)
        anonymous_post = await c.post(
            f"{FIN}/expenses/{expense['id']}/attachments", files={"file": ("r.jpg", jpeg("z"), "image/jpeg")}
        )

    assert anonymous.status_code == 401 and anonymous_post.status_code == 401
    assert unknown.status_code == 404 and malformed.status_code == 422


async def test_a_personal_api_token_can_upload_and_read(db_ready):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        minted = await c.post(f"{API}/account/tokens", json={"name": "jarvis"}, headers=h)
        token = {"Authorization": f"Bearer {minted.json()['token']}"}

        made = await upload_ok(c, token, expense["id"], jpeg("via token"))
        res = await c.get(f"{FIN}/attachments/{made['id']}", headers=token)

    assert res.status_code == 200 and res.content == jpeg("via token")


async def test_a_row_whose_file_has_vanished_answers_404_without_leaking_the_path(
    db_ready, uploads_dir, caplog
):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        made = await upload_ok(c, h, expense["id"], jpeg())
        [path] = files_under(uploads_dir)
        path.unlink()

        with caplog.at_level(logging.DEBUG):
            res = await c.get(f"{FIN}/attachments/{made['id']}", headers=h)

    assert res.status_code == 404
    assert str(uploads_dir) not in res.text and str(uploads_dir) not in app_logs(caplog)


# --- delete ---------------------------------------------------------------------------------------


async def test_delete_removes_the_row_and_the_file(db_ready, uploads_dir):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        keep = await upload_ok(c, h, expense["id"], jpeg("keep"))
        gone = await upload_ok(c, h, expense["id"], jpeg("gone"))

        res = await c.delete(f"{FIN}/attachments/{gone['id']}", headers=h)
        after = await c.get(f"{FIN}/attachments/{gone['id']}", headers=h)
        again = await c.delete(f"{FIN}/attachments/{gone['id']}", headers=h)
        kept = await c.get(f"{FIN}/attachments/{keep['id']}", headers=h)

    assert res.status_code == 204 and res.content == b""
    assert after.status_code == 404 and again.status_code == 404
    assert kept.status_code == 200
    assert [str(r.id) for r in await rows()] == [keep["id"]]
    assert len(files_under(uploads_dir)) == 1


async def test_after_a_delete_the_same_file_can_be_attached_again(db_ready):
    data = jpeg("again")
    async with await client() as c:
        h, expense = await user_with_expense(c)
        first = await upload_ok(c, h, expense["id"], data)
        await c.delete(f"{FIN}/attachments/{first['id']}", headers=h)

        second = await upload(c, h, expense["id"], data)

    assert second.status_code == 201 and second.json()["id"] != first["id"]


async def test_a_file_that_cannot_be_removed_is_logged_without_paths_and_is_not_a_500(
    db_ready, uploads_dir, caplog
):
    from core.storage import LocalFsStorage, get_storage
    from main import app

    class StubbornDisk(LocalFsStorage):
        def delete(self, key: str) -> None:
            raise PermissionError(f"[WinError 5] denied: C:\\private\\folder\\{key}")

    async with await client() as c:
        h, expense = await user_with_expense(c)
        made = await upload_ok(c, h, expense["id"], jpeg())
        app.dependency_overrides[get_storage] = lambda: StubbornDisk(uploads_dir)
        try:
            with caplog.at_level(logging.DEBUG):
                res = await c.delete(f"{FIN}/attachments/{made['id']}", headers=h)
        finally:
            app.dependency_overrides.pop(get_storage, None)

    assert res.status_code == 204
    assert await rows() == []  # the row is gone even though the file stayed
    logged = app_logs(caplog)
    assert "attachment" in logged.lower() and "PermissionError" in logged
    for leaked in ("private", "WinError", made["id"], str(uploads_dir), "u/"):
        assert leaked not in logged, leaked


async def test_a_disk_that_refuses_the_write_leaves_no_row_and_answers_503(db_ready, uploads_dir):
    from core.storage import LocalFsStorage, get_storage
    from main import app

    class FullDisk(LocalFsStorage):
        def put(self, key: str, data: bytes) -> None:
            raise OSError(28, "No space left on device")

    async with await client() as c:
        h, expense = await user_with_expense(c)
        app.dependency_overrides[get_storage] = lambda: FullDisk(uploads_dir)
        try:
            res = await upload(c, h, expense["id"], jpeg())
        finally:
            app.dependency_overrides.pop(get_storage, None)

    assert res.status_code == 503, res.text
    assert await rows() == [] and files_under(uploads_dir) == []


# --- isolation ------------------------------------------------------------------------------------


async def test_someone_elses_expense_and_attachment_answer_404_on_every_route(db_ready, uploads_dir):
    async with await client() as c:
        owner, expense = await user_with_expense(c)
        made = await upload_ok(c, owner, expense["id"], jpeg("mine"))
        intruder = await register(c)

        post = await upload(c, intruder, expense["id"], jpeg("sneaky"))
        get = await c.get(f"{FIN}/attachments/{made['id']}", headers=intruder)
        delete = await c.delete(f"{FIN}/attachments/{made['id']}", headers=intruder)
        still_there = await c.get(f"{FIN}/attachments/{made['id']}", headers=owner)

    assert (post.status_code, get.status_code, delete.status_code) == (404, 404, 404)
    assert still_there.status_code == 200 and still_there.content == jpeg("mine")
    assert len(await rows()) == 1 and len(files_under(uploads_dir)) == 1


async def test_a_foreign_expense_is_404_even_for_a_bad_file_or_an_oversized_declaration(db_ready):
    body = CountingBody([_part_head(), jpeg(), CLOSE])
    async with await client() as c:
        _, expense = await user_with_expense(c)
        intruder = await register(c)

        bad_type = await upload(c, intruder, expense["id"], GIF)
        huge = await c.post(
            f"{FIN}/expenses/{expense['id']}/attachments",
            content=body,
            headers={**intruder, **_multipart_headers(length=2 * MAX_BYTES)},
        )

    assert bad_type.status_code == 404  # nothing about the file is judged before ownership
    assert huge.status_code == 404 and body.pulled == 0


# --- expense payloads carry the attachment list ------------------------------------------------------


async def test_expense_responses_list_their_attachments(db_ready):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        assert expense["attachments"] == []
        project_id = expense["project_id"]
        first = await upload_ok(c, h, expense["id"], jpeg("1"), name="secret-name.jpg")
        second = await upload_ok(c, h, expense["id"], png("2"), name="x.png")
        bare = await another_expense(c, h, project_id)

        listed = await c.get(f"{FIN}/projects/{project_id}/expenses", headers=h)
        patched = await c.patch(f"{FIN}/expenses/{expense['id']}", json={"note": "n"}, headers=h)

    by_id = {e["id"]: e for e in listed.json()["items"]}
    attachments = by_id[expense["id"]]["attachments"]
    assert [a["id"] for a in attachments] == [first["id"], second["id"]]  # upload order
    assert all(set(a) == BRIEF_KEYS for a in attachments)
    assert attachments[0]["mime"] == "image/jpeg" and attachments[1]["mime"] == "image/png"
    assert by_id[bare["id"]]["attachments"] == []
    assert [a["id"] for a in patched.json()["attachments"]] == [first["id"], second["id"]]
    assert "secret-name" not in listed.text  # the client's file name is only in the upload answer


async def test_replaying_an_expense_post_returns_its_attachments(db_ready):
    import uuid

    client_id = str(uuid.uuid4())
    async with await client() as c:
        h = await register(c)
        project = await make_project(c, h, "Replay", "USD")
        body = {"amount": "3", "currency": "USD", "spent_on": "2024-03-29", "client_id": client_id}
        created = await c.post(f"{FIN}/projects/{project['id']}/expenses", json=body, headers=h)
        await upload_ok(c, h, created.json()["id"], jpeg())

        replay = await c.post(f"{FIN}/projects/{project['id']}/expenses", json=body, headers=h)

    assert replay.status_code == 200
    assert len(replay.json()["attachments"]) == 1


async def test_projects_and_reports_carry_no_attachment_counters(db_ready):
    async with await client() as c:
        h, expense = await user_with_expense(c)
        await upload_ok(c, h, expense["id"], jpeg())

        project = (await c.get(f"{FIN}/projects/{expense['project_id']}", headers=h)).json()
        report = (await c.get(f"{FIN}/projects/{expense['project_id']}/report", headers=h)).json()

    assert "attachments_count" not in project and "attachments_count" not in report
    assert "attachments" not in project and "attachments" not in report


# --- encryption at rest ---------------------------------------------------------------------------------


async def test_with_a_fernet_key_the_file_on_disk_is_encrypted_and_still_served_in_clear(
    db_ready, uploads_dir, fernet_on
):
    plain = jpeg("TOP-SECRET-RECEIPT", 2000)
    async with await client() as c:
        h, expense = await user_with_expense(c)

        made = await upload_ok(c, h, expense["id"], plain)
        res = await c.get(f"{FIN}/attachments/{made['id']}", headers=h)
        again = await upload(c, h, expense["id"], plain)

    [path] = files_under(uploads_dir)
    on_disk = path.read_bytes()
    assert on_disk != plain and b"TOP-SECRET-RECEIPT" not in on_disk
    assert not on_disk.startswith(b"\xff\xd8\xff")
    assert Fernet(fernet_on.encode()).decrypt(on_disk) == plain
    assert res.status_code == 200 and res.content == plain
    assert made["size_bytes"] == len(plain)  # the size of the picture, not of the sealed file
    assert again.status_code == 200 and again.json()["id"] == made["id"]  # dedupe by the plain bytes


async def test_without_a_key_the_file_is_stored_as_it_is(db_ready, uploads_dir):
    plain = jpeg("plain-on-disk", 500)
    assert settings.fernet_key == ""
    async with await client() as c:
        h, expense = await user_with_expense(c)

        await upload_ok(c, h, expense["id"], plain)

    [path] = files_under(uploads_dir)
    assert path.read_bytes() == plain


# --- logs -----------------------------------------------------------------------------------------------


async def test_logs_hold_neither_file_names_nor_amounts(db_ready, caplog):
    async with await client() as c:
        h = await register(c)
        project = await make_project(c, h, "Логи", "USD")
        with caplog.at_level(logging.DEBUG):
            expense = await add_ok(c, h, project["id"], "7654.32", "USD", days_from_today(0))
            made = await upload_ok(c, h, expense["id"], jpeg(), name="Zq7-private-receipt.jpg")
            await c.get(f"{FIN}/attachments/{made['id']}", headers=h)
            await upload(c, h, expense["id"], GIF, name="Zq7-bad.gif")
            await c.delete(f"{FIN}/attachments/{made['id']}", headers=h)

    logged = app_logs(caplog)
    assert "Zq7" not in logged and "7654.32" not in logged
