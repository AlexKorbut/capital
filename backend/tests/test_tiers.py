"""Срез 6 — subscription tier enforcement.

Free users hit a 403 (with a machine-readable ``code`` + ``upgrade_url``) on
gated features and over-quota actions; paid users pass.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from core.tiers import (
    check_asset_count,
    enforce_snapshot_quota,
    limits_for,
    plan_of,
    require_scenarios,
)
from models.user import User


def _user(plan="free", expires=None) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@t.test",
        password_hash="x",
        base_currency="USD",
        subscription=plan,
        sub_expires_at=expires,
    )


def test_plan_of_degrades_expired_paid_to_free():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    assert plan_of(_user("pro", past)) == "free"
    future = datetime.now(timezone.utc) + timedelta(days=10)
    assert plan_of(_user("pro", future)) == "pro"
    # Paid with no expiry stays paid (lifetime / managed by Stripe).
    assert plan_of(_user("business", None)) == "business"


def test_require_scenarios_gates_free_allows_pro():
    with pytest.raises(HTTPException) as ei:
        require_scenarios(_user("free"))
    assert ei.value.status_code == 403
    assert ei.value.detail["code"] == "subscription_required"
    assert ei.value.detail["upgrade_url"] == "/pricing"

    pro = _user("pro", datetime.now(timezone.utc) + timedelta(days=5))
    assert require_scenarios(pro) is pro


def test_check_asset_count_enforces_free_cap():
    free_limits = limits_for(_user("free"))  # 10 assets
    check_asset_count(free_limits, 10)  # ok
    with pytest.raises(HTTPException) as ei:
        check_asset_count(free_limits, 11)
    assert ei.value.detail["code"] == "quota_exceeded"

    # Business is unlimited.
    biz_limits = limits_for(_user("business"))
    check_asset_count(biz_limits, 10_000)


async def test_enforce_snapshot_quota_blocks_fourth_free_snapshot(db_ready):
    from core.db import SessionLocal
    from models.snapshot import Snapshot

    uid = uuid.uuid4()
    async with SessionLocal() as s:
        user = User(
            id=uid, email=f"{uid}@t.test", password_hash="x",
            base_currency="USD", subscription="free",
        )
        s.add(user)
        for _ in range(3):  # Free allows 3 confirmed snapshots/month.
            s.add(
                Snapshot(
                    id=uuid.uuid4(), user_id=uid, total_usd=Decimal("100"),
                    base_currency="USD", is_confirmed=True,
                )
            )
        await s.commit()

    async with SessionLocal() as s:
        user = await s.get(User, uid)
        with pytest.raises(HTTPException) as ei:
            await enforce_snapshot_quota(user=user, db=s)
        assert ei.value.status_code == 403
        assert ei.value.detail["code"] == "quota_exceeded"


async def test_enforce_snapshot_quota_passes_for_pro(db_ready):
    from core.db import SessionLocal
    from models.snapshot import Snapshot

    uid = uuid.uuid4()
    async with SessionLocal() as s:
        user = User(
            id=uid, email=f"{uid}@t.test", password_hash="x", base_currency="USD",
            subscription="pro",
            sub_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        s.add(user)
        for _ in range(5):
            s.add(
                Snapshot(
                    id=uuid.uuid4(), user_id=uid, total_usd=Decimal("100"),
                    base_currency="USD", is_confirmed=True,
                )
            )
        await s.commit()

    async with SessionLocal() as s:
        user = await s.get(User, uid)
        # Pro has unlimited snapshots → no raise, returns the user.
        assert await enforce_snapshot_quota(user=user, db=s) is user
