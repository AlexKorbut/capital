"""Receipt photos on expenses (fin-02): type by signature, de-duplication, per-expense and
per-user limits, and the pairing of a database row with a file in ``core.storage``.

Rules worth knowing:

* The type is decided by the file's own first bytes (JPEG, PNG, WEBP with a consistent RIFF header
  and a known first chunk) -- never by the name or the ``Content-Type`` the client declared.
  HEIC and everything else is refused (415).
* The storage key is built here from ids only; the client's file name is kept as
  ``original_name`` (display only, encrypted at rest) and goes into no path and no header.
* Bytes are sealed with Fernet when ``FERNET_KEY`` is set, otherwise stored as they are -- and in
  production an upload without a key is refused (503) instead of quietly storing pictures in the clear.
* Every read is verified: length and sha256 of what comes out of the file must match the record. A
  mismatch (damage, a lost or changed key) is a 500 "attachment is damaged or the key is wrong",
  never a stream of ciphertext served as an image. An old file stored in the clear before a key was
  configured still verifies (its hash matches), which is how it differs from a failed decryption.
* All uploads of one user are serialised by a lock on the user's row (a no-op on SQLite), so the
  per-expense cap, the per-user quotas and the duplicate check cannot race each other.
* A row is flushed first (constraints speak before any file exists), then the file is written,
  then the transaction commits; a failure after the write removes the file again.
* At most ``MAX_CONCURRENT_UPLOADS`` uploads are in flight at once (each holds up to 10 MB, plus the
  encrypted copy): an upload that cannot get a slot within ``UPLOAD_SLOT_WAIT_SECONDS`` gets a 503.

Nothing here logs file names, keys, paths, ids or amounts.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import unicodedata
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Select, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core import quotas
from core.config import settings
from core.crypto import decrypt_bytes, encrypt_bytes
from core.storage import InvalidStorageKey, StorageBackend, remove_files
from models.finance import FinAttachment, FinExpense
from models.user import User
from services.finance import (
    AttachmentCorrupted,
    Conflict,
    NotFound,
    PayloadTooLarge,
    StorageUnavailable,
    UnsupportedMedia,
    as_uuid,
)

logger = logging.getLogger("kapital.attachments")

BYTES_IN_MB = 1024 * 1024
MAX_ATTACHMENT_BYTES = 10 * BYTES_IN_MB
MAX_ATTACHMENTS_PER_EXPENSE = 5
ATTACHMENT_MIME = ("image/jpeg", "image/png", "image/webp")
ORIGINAL_NAME_MAX = 200

MAX_CONCURRENT_UPLOADS = 4
UPLOAD_SLOT_WAIT_SECONDS = 15.0

UNSUPPORTED_MESSAGE = "поддерживаются JPEG, PNG и WEBP — сожмите фото на телефоне"
TOO_MANY_MESSAGE = f"К трате можно приложить не больше {MAX_ATTACHMENTS_PER_EXPENSE} файлов"
CORRUPTED_MESSAGE = "вложение повреждено или ключ не тот"
NOT_CONFIGURED_MESSAGE = "хранилище вложений не настроено (FERNET_KEY)"
BUSY_MESSAGE = "Сервер занят загрузками, повторите через несколько секунд"

_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_JPEG = b"\xff\xd8\xff"
_PNG = b"\x89PNG\r\n\x1a\n"
_WEBP_CHUNKS = (b"VP8 ", b"VP8L", b"VP8X")  # lossy, lossless, extended: the only ways a WEBP begins
_RIFF_HEADER = 12  # "WEBP" + one chunk header (type + size): the least a container can claim


def _is_webp(data: bytes | bytearray) -> bool:
    """``RIFF`` + size + ``WEBP`` + a known first chunk, with a size field that fits the data.

    The size field counts the bytes after itself; trailing bytes beyond it are tolerated (the
    decoder ignores them), a claim of MORE bytes than the file has is a truncated or lying file.
    """
    if len(data) < 16 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return False
    riff_size = int.from_bytes(data[4:8], "little")
    return (
        riff_size >= _RIFF_HEADER
        and riff_size + 8 <= len(data)
        and bytes(data[12:16]) in _WEBP_CHUNKS
    )


def sniff_mime(data: bytes | bytearray) -> str | None:
    """JPEG / PNG / WEBP by signature, or ``None``. Nothing but the bytes is trusted."""
    if data.startswith(_JPEG):
        return "image/jpeg"
    if data.startswith(_PNG):
        return "image/png"
    if _is_webp(data):
        return "image/webp"
    return None


def extension_for(mime: str) -> str:
    return _EXTENSIONS[mime]


def storage_key(user_id: uuid.UUID, expense_id: uuid.UUID, attachment_id: uuid.UUID, mime: str) -> str:
    """The only way a key is made: ids and a fixed extension, nothing from the client."""
    return f"u/{user_id}/{expense_id}/{attachment_id}.{extension_for(mime)}"


def clean_original_name(raw: str | None) -> str | None:
    """The client's name for display: last path part, no control characters, at most 200 chars."""
    if raw is None:
        return None
    name = unicodedata.normalize("NFC", raw.replace("\\", "/").rsplit("/", 1)[-1])
    name = "".join(ch for ch in name if not unicodedata.category(ch).startswith("C")).strip()
    if name in {"", ".", ".."}:
        return None
    return name[:ORIGINAL_NAME_MAX]


def _digest(data: bytes | bytearray) -> str:
    return hashlib.sha256(data).hexdigest()


# --- encryption at rest: required in production ---------------------------------------------------


def require_encryption_ready() -> None:
    """Production never stores receipts in the clear: no key, no uploads (503)."""
    if settings.is_prod and not settings.fernet_key:
        raise StorageUnavailable(NOT_CONFIGURED_MESSAGE)


def log_encryption_state() -> None:
    """One line at startup when files would not be encrypted."""
    if settings.fernet_key:
        return
    if settings.is_prod:
        logger.warning("receipt uploads are disabled: FERNET_KEY is not set (production)")
    else:
        logger.warning("receipt files are stored unencrypted: FERNET_KEY is not set (development)")


# --- concurrency: a few uploads at a time ----------------------------------------------------------

# One semaphore per running event loop (production has exactly one; each test gets its own loop).
_slots: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None


def _upload_semaphore() -> asyncio.Semaphore:
    global _slots
    loop = asyncio.get_running_loop()
    if _slots is None or _slots[0] is not loop:
        _slots = (loop, asyncio.Semaphore(MAX_CONCURRENT_UPLOADS))
    return _slots[1]


@asynccontextmanager
async def upload_slot() -> AsyncIterator[None]:
    """Hold one of the upload slots; a full house for too long is a 503 ("retry in a moment")."""
    semaphore = _upload_semaphore()
    try:
        await asyncio.wait_for(semaphore.acquire(), UPLOAD_SLOT_WAIT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise StorageUnavailable(BUSY_MESSAGE) from exc
    try:
        yield
    finally:
        semaphore.release()


# --- reads ---------------------------------------------------------------------------------


async def attachments_by_expense(
    db: AsyncSession, user_id: Any, expense_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[FinAttachment]]:
    """The attachments of the given expenses, oldest first, grouped by expense."""
    grouped: dict[uuid.UUID, list[FinAttachment]] = {}
    if not expense_ids:
        return grouped
    rows = await db.scalars(
        select(FinAttachment)
        .where(
            FinAttachment.user_id == as_uuid(user_id),
            FinAttachment.expense_id.in_(expense_ids),
        )
        .order_by(FinAttachment.created_at, FinAttachment.id)
    )
    for row in rows:
        grouped.setdefault(row.expense_id, []).append(row)
    return grouped


async def get_attachment(db: AsyncSession, user_id: Any, attachment_id: uuid.UUID) -> FinAttachment:
    row = await db.scalar(
        select(FinAttachment).where(
            FinAttachment.id == attachment_id, FinAttachment.user_id == as_uuid(user_id)
        )
    )
    if row is None:
        raise NotFound("Вложение не найдено")
    return row


class _Mismatch(Exception):
    """What came out of the file is not what was stored (``what``: "size" or "sha256")."""

    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


def _load_verified(storage: StorageBackend, key: str, size_bytes: int, sha256: str) -> bytes:
    data = decrypt_bytes(storage.get(key))
    if len(data) != size_bytes:
        raise _Mismatch("size")
    if _digest(data) != sha256:
        raise _Mismatch("sha256")
    return data


async def read_attachment(attachment: FinAttachment, storage: StorageBackend) -> bytes:
    """The plain bytes of the picture, checked against the record (length and sha256)."""
    try:
        return await asyncio.to_thread(
            _load_verified, storage, attachment.storage_key, attachment.size_bytes, attachment.sha256
        )
    except _Mismatch as exc:
        logger.error("attachment integrity check failed (%s)", exc.what)
        raise AttachmentCorrupted(CORRUPTED_MESSAGE) from exc
    except (FileNotFoundError, InvalidStorageKey) as exc:
        logger.warning("attachment file is missing (%s)", type(exc).__name__)
        raise NotFound("Файл вложения не найден") from exc
    except OSError as exc:
        logger.warning("attachment file could not be read (%s)", type(exc).__name__)
        raise StorageUnavailable("Хранилище файлов недоступно, попробуйте позже") from exc


# --- writes --------------------------------------------------------------------------------


async def _duplicate(db: AsyncSession, expense_id: uuid.UUID, sha256: str) -> FinAttachment | None:
    return await db.scalar(
        select(FinAttachment).where(
            FinAttachment.expense_id == expense_id, FinAttachment.sha256 == sha256
        )
    )


def _store(storage: StorageBackend, key: str, data: bytes | bytearray) -> None:
    storage.put(key, encrypt_bytes(data))


def user_lock_statement(user_id: uuid.UUID) -> Select[Any]:
    """Lock the user's row for the length of the upload transaction.

    ``FOR NO KEY UPDATE`` (Postgres) conflicts with itself but NOT with the ``FOR KEY SHARE`` an
    insert into any table that references users takes: uploads of one user queue up, nothing else
    does. SQLite ignores the clause (it serialises writers anyway).
    """
    return select(User.id).where(User.id == user_id).with_for_update(key_share=True)


async def _check_user_quota(db: AsyncSession, user_id: uuid.UUID, new_bytes: int) -> None:
    held, used = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(FinAttachment.size_bytes), 0)).where(
                FinAttachment.user_id == user_id
            )
        )
    ).one()
    if held >= quotas.MAX_USER_ATTACHMENTS:
        raise Conflict(f"Достигнут предел: не больше {quotas.MAX_USER_ATTACHMENTS} вложений на пользователя")
    if int(used) + new_bytes > quotas.MAX_USER_ATTACHMENT_BYTES:
        limit_mb = quotas.MAX_USER_ATTACHMENT_BYTES / BYTES_IN_MB
        raise PayloadTooLarge(
            f"Достигнут предел хранилища вложений: не больше {limit_mb:g} МБ суммарно. "
            "Удалите ненужные фото чеков"
        )


async def add_attachment(
    db: AsyncSession,
    expense: FinExpense,
    *,
    data: bytes | bytearray,
    original_name: str | None,
    storage: StorageBackend,
) -> tuple[FinAttachment, bool]:
    """Attach ``data`` to ``expense``. Returns ``(attachment, created)``.

    The same bytes on the same expense come back as the existing attachment (``created`` is
    False) -- before any limit is looked at, so repeating an upload is always safe.
    """
    mime = sniff_mime(data)
    if mime is None:
        raise UnsupportedMedia(UNSUPPORTED_MESSAGE)
    sha256 = await asyncio.to_thread(_digest, data)
    user_id, expense_id = expense.user_id, expense.id

    await db.execute(user_lock_statement(user_id))  # held until the commit below
    existing = await _duplicate(db, expense_id, sha256)
    if existing is not None:
        return existing, False
    on_expense = await db.scalar(
        select(func.count()).select_from(FinAttachment).where(FinAttachment.expense_id == expense_id)
    )
    if (on_expense or 0) >= MAX_ATTACHMENTS_PER_EXPENSE:
        raise Conflict(TOO_MANY_MESSAGE)
    await _check_user_quota(db, user_id, len(data))

    attachment_id = uuid.uuid4()
    key = storage_key(user_id, expense_id, attachment_id, mime)
    row = FinAttachment(
        id=attachment_id,
        user_id=user_id,
        expense_id=expense_id,
        storage_key=key,
        mime=mime,
        size_bytes=len(data),
        sha256=sha256,
        original_name=clean_original_name(original_name),
        # Set here (not by the database): microsecond precision keeps "oldest first" stable.
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        winner = await _duplicate(db, expense_id, sha256)  # lost a race for the same picture
        if winner is None:
            raise NotFound("Трата не найдена") from exc  # the expense went away meanwhile
        return winner, False

    try:
        await asyncio.to_thread(_store, storage, key, data)
    except (OSError, InvalidStorageKey) as exc:
        await db.rollback()
        logger.warning("attachment file could not be written (%s)", type(exc).__name__)
        raise StorageUnavailable("Не удалось сохранить файл, попробуйте позже") from exc
    try:
        await db.commit()
    except BaseException:
        await asyncio.to_thread(remove_files, storage, [key])  # no row will ever point at it
        raise
    await db.refresh(row)  # read back as the database holds it: the same shape as every later read
    return row, True


async def delete_attachment(
    db: AsyncSession, attachment: FinAttachment, storage: StorageBackend
) -> None:
    """Drop the row, then (after the commit) the file."""
    key = attachment.storage_key
    await db.execute(
        delete(FinAttachment).where(
            FinAttachment.id == attachment.id, FinAttachment.user_id == attachment.user_id
        )
    )
    await db.commit()
    await asyncio.to_thread(remove_files, storage, [key])
