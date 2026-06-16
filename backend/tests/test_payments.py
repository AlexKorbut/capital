"""Срез 6 — payment webhooks are the source of truth + idempotent.

We don't hit Stripe/CoinGate; we exercise event parsing and ``apply`` directly:
  - Stripe ``checkout.session.completed`` → user upgraded to Pro.
  - replaying the same provider event is a no-op (idempotency).
  - ``customer.subscription.deleted`` → downgrade to Free.
  - CoinGate HMAC signature: valid ``paid`` grants a prepaid period; bad sig 400.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import pytest

from models.user import User
from services.payments.apply import apply_subscription_update
from services.payments.coingate_provider import CoinGateProvider
from services.payments.stripe_provider import StripeProvider


def _stripe_event(etype: str, obj: dict, event_id: str) -> dict:
    return {"id": event_id, "type": etype, "data": {"object": obj}}


def test_stripe_parse_checkout_completed_maps_user_and_plan():
    uid = str(uuid.uuid4())
    event = _stripe_event(
        "checkout.session.completed",
        {
            "client_reference_id": uid,
            "metadata": {"user_id": uid, "plan": "pro"},
            "amount_total": 900,
            "currency": "usd",
            "customer": "cus_123",
        },
        "evt_1",
    )
    upd = StripeProvider._parse_event(StripeProvider.__new__(StripeProvider), event)
    assert upd.status == "active"
    assert upd.plan == "pro"
    assert upd.user_id == uid
    assert upd.stripe_customer_id == "cus_123"
    assert upd.amount_usd == "9.00"
    assert upd.provider_ref == "evt_1"


async def test_apply_upgrade_then_idempotent_replay(db_ready):
    from sqlalchemy import func, select

    from core.db import SessionLocal
    from models.billing import SubscriptionEvent

    uid = uuid.uuid4()
    async with SessionLocal() as s:
        s.add(
            User(id=uid, email=f"{uid}@t.test", password_hash="x",
                 base_currency="USD", subscription="free")
        )
        await s.commit()

    event = _stripe_event(
        "checkout.session.completed",
        {"client_reference_id": str(uid),
         "metadata": {"user_id": str(uid), "plan": "pro"},
         "amount_total": 900, "currency": "usd", "customer": "cus_abc"},
        "evt_dup",
    )
    upd = StripeProvider._parse_event(StripeProvider.__new__(StripeProvider), event)

    async with SessionLocal() as s:
        assert await apply_subscription_update(s, upd) is True
    async with SessionLocal() as s:
        # Replaying the same event id changes nothing.
        assert await apply_subscription_update(s, upd) is False

    async with SessionLocal() as s:
        user = await s.get(User, uid)
        assert user.subscription == "pro"
        assert user.stripe_customer_id == "cus_abc"
        count = await s.scalar(
            select(func.count()).select_from(SubscriptionEvent)
            .where(SubscriptionEvent.user_id == uid)
        )
        assert count == 1  # only one ledger row despite two applies


async def test_apply_subscription_deleted_downgrades(db_ready):
    from core.db import SessionLocal

    uid = uuid.uuid4()
    async with SessionLocal() as s:
        s.add(
            User(id=uid, email=f"{uid}@t.test", password_hash="x",
                 base_currency="USD", subscription="pro")
        )
        await s.commit()

    deleted = _stripe_event(
        "customer.subscription.deleted",
        {"metadata": {"user_id": str(uid), "plan": "pro"},
         "current_period_end": int(datetime.now(timezone.utc).timestamp()),
         "customer": "cus_x"},
        "evt_del",
    )
    upd = StripeProvider._parse_event(StripeProvider.__new__(StripeProvider), deleted)
    assert upd.status == "cancelled"
    assert upd.plan == "free"

    async with SessionLocal() as s:
        assert await apply_subscription_update(s, upd) is True
    async with SessionLocal() as s:
        user = await s.get(User, uid)
        assert user.subscription == "free"
        assert user.sub_expires_at is None


def test_coingate_verify_paid_grants_period_and_rejects_bad_sig(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "coingate_webhook_secret", "topsecret")
    monkeypatch.setattr(settings, "crypto_pro_months", 2)

    uid = str(uuid.uuid4())
    body = json.dumps(
        {"order_id": f"{uid}:pro:2", "status": "paid", "token": "tok_1",
         "price_amount": "9.00"}
    ).encode("utf-8")
    good_sig = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()

    provider = CoinGateProvider()
    upd = provider.verify_and_parse(payload=body, headers={"x-coingate-signature": good_sig})
    assert upd.status == "active"
    assert upd.plan == "pro"
    assert upd.user_id == uid
    assert upd.provider_ref == "tok_1"
    assert upd.expires_at is not None and upd.expires_at > datetime.now(timezone.utc)

    with pytest.raises(ValueError):
        provider.verify_and_parse(payload=body, headers={"x-coingate-signature": "deadbeef"})
