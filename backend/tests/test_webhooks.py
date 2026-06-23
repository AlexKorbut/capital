"""Payment webhook security — signature, fail-closed, idempotency, amount.

These cover the highest-risk untested surface: a forged/unsigned/underpaid
webhook must NOT grant a paid plan, and a replayed event must apply once.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

API = "/api/v1"


async def _client() -> AsyncClient:
    from main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _register(client: AsyncClient) -> tuple[dict, str]:
    email = f"{uuid.uuid4().hex}@example.com"
    res = await client.post(
        f"{API}/auth/register", json={"email": email, "password": "supersecret1"}
    )
    assert res.status_code == 201, res.text
    return res.json(), email


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@pytest.fixture
def coingate_secret(monkeypatch, non_demo):
    from core.config import settings

    monkeypatch.setattr(settings, "coingate_webhook_secret", "testsecret")
    return "testsecret"


def _coingate_body(
    uid: str, *, status: str = "paid", price: str = "9.00", token: str | None = None
) -> bytes:
    return json.dumps(
        {
            "status": status,
            "order_id": f"{uid}:pro:1",
            "token": token or f"tok-{uuid.uuid4().hex}",
            "price_amount": price,
            "price_currency": "USD",
        }
    ).encode("utf-8")


async def _me(client: AsyncClient, access: str) -> dict:
    r = await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {access}"})
    return r.json()


async def test_coingate_valid_signature_grants_pro(db_ready, coingate_secret):
    async with await _client() as client:
        reg, _ = await _register(client)
        uid = reg["user"]["id"]
        body = _coingate_body(uid)
        res = await client.post(
            f"{API}/webhooks/coingate",
            content=body,
            headers={"X-CoinGate-Signature": _sign(body, coingate_secret)},
        )
        assert res.status_code == 200, res.text
        assert res.json()["applied"] is True
        me = await _me(client, reg["tokens"]["access_token"])
        assert me["subscription"] == "pro"
        assert me["sub_expires_at"] is not None


async def test_coingate_bad_signature_rejected(db_ready, coingate_secret):
    async with await _client() as client:
        reg, _ = await _register(client)
        body = _coingate_body(reg["user"]["id"])
        res = await client.post(
            f"{API}/webhooks/coingate",
            content=body,
            headers={"X-CoinGate-Signature": "deadbeef"},
        )
        assert res.status_code == 400
        me = await _me(client, reg["tokens"]["access_token"])
        assert me["subscription"] == "free"


async def test_coingate_unsigned_failclosed_in_prod_path(db_ready, monkeypatch, non_demo):
    """No configured secret + non-demo ⇒ reject (never trust an unsigned callback)."""
    from core.config import settings

    monkeypatch.setattr(settings, "coingate_webhook_secret", "")
    async with await _client() as client:
        reg, _ = await _register(client)
        body = _coingate_body(reg["user"]["id"])
        res = await client.post(f"{API}/webhooks/coingate", content=body)
        assert res.status_code == 400


async def test_coingate_underpaid_not_granted(db_ready, coingate_secret):
    async with await _client() as client:
        reg, _ = await _register(client)
        uid = reg["user"]["id"]
        body = _coingate_body(uid, price="0.01")  # far below crypto_pro_price_usd
        res = await client.post(
            f"{API}/webhooks/coingate",
            content=body,
            headers={"X-CoinGate-Signature": _sign(body, coingate_secret)},
        )
        assert res.status_code == 200
        # An underpaid order records a "failed" event but must NOT upgrade the user.
        me = await _me(client, reg["tokens"]["access_token"])
        assert me["subscription"] == "free"
        assert me["sub_expires_at"] is None


async def test_coingate_underpaid_then_topup_grants(db_ready, coingate_secret):
    """An underpaid callback must NOT burn the order token: a later full payment
    for the same order (same token) must still grant Pro."""
    async with await _client() as client:
        reg, _ = await _register(client)
        uid = reg["user"]["id"]
        token = f"tok-{uuid.uuid4().hex}"
        underpaid = _coingate_body(uid, price="0.01", token=token)
        r1 = await client.post(
            f"{API}/webhooks/coingate",
            content=underpaid,
            headers={"X-CoinGate-Signature": _sign(underpaid, coingate_secret)},
        )
        assert r1.status_code == 200 and r1.json()["applied"] is False
        # Same order, topped up to full price, same token → must grant now.
        paid = _coingate_body(uid, price="9.00", token=token)
        r2 = await client.post(
            f"{API}/webhooks/coingate",
            content=paid,
            headers={"X-CoinGate-Signature": _sign(paid, coingate_secret)},
        )
        assert r2.status_code == 200 and r2.json()["applied"] is True
        me = await _me(client, reg["tokens"]["access_token"])
        assert me["subscription"] == "pro"


async def test_coingate_idempotent_on_replay(db_ready, coingate_secret):
    async with await _client() as client:
        reg, _ = await _register(client)
        body = _coingate_body(reg["user"]["id"])
        sig = _sign(body, coingate_secret)
        first = await client.post(
            f"{API}/webhooks/coingate", content=body, headers={"X-CoinGate-Signature": sig}
        )
        second = await client.post(
            f"{API}/webhooks/coingate", content=body, headers={"X-CoinGate-Signature": sig}
        )
        assert first.json()["applied"] is True
        assert second.json()["applied"] is False  # same token ⇒ no double-apply


async def test_stripe_bad_signature_rejected(db_ready, monkeypatch, non_demo):
    from core.config import settings

    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")
    async with await _client() as client:
        res = await client.post(
            f"{API}/webhooks/stripe",
            content=b'{"type":"checkout.session.completed"}',
            headers={"Stripe-Signature": "t=1,v1=bogus"},
        )
        assert res.status_code == 400
