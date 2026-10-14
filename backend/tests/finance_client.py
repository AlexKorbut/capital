"""Shared HTTP helpers for the finance-project API tests."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from httpx import ASGITransport, AsyncClient, Response

API = "/api/v1"
FIN = f"{API}/finance"


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def days_from_today(days: int) -> str:
    return (today_utc() + timedelta(days=days)).isoformat()


async def client() -> AsyncClient:
    from main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def register(c: AsyncClient) -> dict[str, str]:
    """Register a fresh user; returns ready-to-use auth headers (plus the user id)."""
    email = f"{uuid.uuid4().hex}@example.com"
    res = await c.post(f"{API}/auth/register", json={"email": email, "password": "supersecret1"})
    assert res.status_code == 201, res.text
    data = res.json()
    return {"Authorization": f"Bearer {data['tokens']['access_token']}"}


async def make_project(c: AsyncClient, h: dict, name: str = "Машина", base: str = "USD", **extra) -> dict:
    res = await c.post(f"{FIN}/projects", json={"name": name, "base_currency": base, **extra}, headers=h)
    assert res.status_code == 201, res.text
    return res.json()


async def add_expense(
    c: AsyncClient,
    h: dict,
    project_id: str,
    amount: str,
    currency: str,
    spent_on: str,
    **extra,
) -> Response:
    body = {"amount": amount, "currency": currency, "spent_on": spent_on, **extra}
    return await c.post(f"{FIN}/projects/{project_id}/expenses", json=body, headers=h)


async def add_ok(c: AsyncClient, h: dict, project_id: str, amount: str, currency: str, spent_on: str, **extra) -> dict:
    res = await add_expense(c, h, project_id, amount, currency, spent_on, **extra)
    assert res.status_code in (200, 201), res.text
    return res.json()
