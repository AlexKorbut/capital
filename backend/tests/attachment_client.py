"""Shared helpers for the receipt-attachment (fin-02) tests."""
from __future__ import annotations

import uuid
from pathlib import Path

from httpx import AsyncClient, Response
from sqlalchemy import select

from core.db import SessionLocal
from models.finance import FinAttachment
from tests.finance_client import FIN, add_ok, days_from_today, make_project, register

MAX_BYTES = 10 * 1024 * 1024
UNSUPPORTED_TEXT = "поддерживаются JPEG, PNG и WEBP — сожмите фото на телефоне"

_JPEG_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
_PNG_HEAD = b"\x89PNG\r\n\x1a\n"
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64


def _pad(head: bytes, tag: str, size: int) -> bytes:
    data = (head + tag.encode()).ljust(size, b"\x00")
    assert len(data) == size, "size too small for the header"
    return data


def jpeg(tag: str = "a", size: int = 128) -> bytes:
    """A byte string with a valid JPEG signature; ``tag`` makes the content distinct."""
    return _pad(_JPEG_HEAD, tag, size)


def png(tag: str = "a", size: int = 128) -> bytes:
    return _pad(_PNG_HEAD, tag, size)


def webp(tag: str = "a", size: int = 128) -> bytes:
    head = b"RIFF" + (size - 8).to_bytes(4, "little") + b"WEBPVP8 "
    return _pad(head, tag, size)


async def upload(
    c: AsyncClient,
    headers: dict,
    expense_id: str,
    data: bytes,
    name: str = "receipt.jpg",
    content_type: str = "image/jpeg",
) -> Response:
    return await c.post(
        f"{FIN}/expenses/{expense_id}/attachments",
        files={"file": (name, data, content_type)},
        headers=headers,
    )


async def upload_ok(c: AsyncClient, headers: dict, expense_id: str, data: bytes, **kw) -> dict:
    res = await upload(c, headers, expense_id, data, **kw)
    assert res.status_code in (200, 201), res.text
    return res.json()


async def user_with_expense(c: AsyncClient, name: str = "Машина") -> tuple[dict, dict]:
    """A fresh user with one project and one USD expense (no FX lookup needed)."""
    headers = await register(c)
    project = await make_project(c, headers, name, "USD")
    expense = await add_ok(c, headers, project["id"], "10", "USD", days_from_today(0))
    return headers, expense


async def another_expense(c: AsyncClient, headers: dict, project_id: str) -> dict:
    return await add_ok(c, headers, project_id, "5", "USD", days_from_today(0))


async def rows(expense_id: str | None = None) -> list[FinAttachment]:
    async with SessionLocal() as db:
        stmt = select(FinAttachment).order_by(FinAttachment.created_at, FinAttachment.id)
        if expense_id is not None:
            stmt = stmt.where(FinAttachment.expense_id == uuid.UUID(expense_id))
        return list(await db.scalars(stmt))


def app_logs(caplog) -> str:
    """The application's own log lines. httpx logs every request URL (ids included) at INFO,
    which says nothing about what the server logs."""
    skipped = ("httpx", "httpcore", "asyncio")
    return "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith(skipped))


def files_under(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []
