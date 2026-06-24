"""Subscription tiers — plan limits + FastAPI enforcement (plan §3.2).

Source of truth for *what each plan can do*. Endpoints attach the dependencies
below to gate features (scenarios, export) and quotas (snapshots/month,
advice/week). Over-limit requests get a 403 whose body carries a machine-
readable ``code`` + ``upgrade_url`` so the SPA can route to /pricing.

Counters are computed from the DB (dev). In prod the same dependencies can read
Redis counters behind the same interface.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db import get_db
from core.deps import get_current_user
from models.advice import AdviceSession
from models.snapshot import Snapshot
from models.user import User

UPGRADE_URL = "/pricing"

Plan = str  # "free" | "pro" | "business"


@dataclass(frozen=True)
class PlanLimits:
    name: Plan
    label: str
    # None means unlimited.
    max_snapshots_per_month: int | None
    max_assets_per_snapshot: int | None
    advice_per_week: int | None
    can_use_scenarios: bool
    can_export: bool


PLANS: dict[Plan, PlanLimits] = {
    "free": PlanLimits(
        name="free",
        label="Free",
        max_snapshots_per_month=3,
        max_assets_per_snapshot=10,
        advice_per_week=1,
        can_use_scenarios=False,
        can_export=False,
    ),
    "pro": PlanLimits(
        name="pro",
        label="Pro",
        max_snapshots_per_month=None,
        max_assets_per_snapshot=100,
        advice_per_week=None,
        can_use_scenarios=True,
        can_export=True,
    ),
    "business": PlanLimits(
        name="business",
        label="Business",
        max_snapshots_per_month=None,
        max_assets_per_snapshot=None,
        advice_per_week=None,
        can_use_scenarios=True,
        can_export=True,
    ),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def plan_of(user: User) -> Plan:
    """Effective plan, accounting for expiry.

    A paid plan whose ``sub_expires_at`` has passed silently degrades to free —
    webhooks (renewals) push the expiry forward while the subscription is live.
    """
    plan = (user.subscription or "free").lower()
    if plan == "free" or plan not in PLANS:
        return "free"
    expires = user.sub_expires_at
    if expires is not None:
        # Compare in UTC; treat naive timestamps (SQLite) as UTC.
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < _now():
            return "free"
    return plan


def limits_for(user: User) -> PlanLimits:
    return PLANS[plan_of(user)]


def _upgrade_exc(*, code: str, message: str, required: Plan = "pro") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": code,
            "message": message,
            "required_plan": required,
            "upgrade_url": UPGRADE_URL,
        },
    )


# --- Quota counters (DB-backed in dev) ----------------------------------------


def _month_start() -> datetime:
    n = _now()
    return datetime(n.year, n.month, 1, tzinfo=timezone.utc)


def _week_start() -> datetime:
    n = _now()
    monday = n - timedelta(days=n.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)


def _threshold(dt: datetime) -> datetime:
    """Match the stored column type: SQLite (dev) keeps naive datetimes, Postgres
    (prod) keeps tz-aware. Comparing the wrong kind silently mis-counts quotas."""
    return dt.replace(tzinfo=None) if settings.is_sqlite else dt


async def _count_snapshots_this_month(db: AsyncSession, user_id) -> int:
    threshold = _threshold(_month_start())
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Snapshot)
            .where(
                Snapshot.user_id == user_id,
                Snapshot.is_confirmed.is_(True),
                Snapshot.created_at >= threshold,
            )
        )
        or 0
    )


async def _count_advice_this_week(db: AsyncSession, user_id) -> int:
    threshold = _threshold(_week_start())
    return int(
        await db.scalar(
            select(func.count())
            .select_from(AdviceSession)
            .where(
                AdviceSession.user_id == user_id,
                AdviceSession.generated_at >= threshold,
            )
        )
        or 0
    )


# --- Enforcement dependencies -------------------------------------------------


def require_scenarios(
    user: User = Depends(get_current_user),
) -> User:
    """Gate the Scenario feature — Free has no access."""
    if not limits_for(user).can_use_scenarios:
        raise _upgrade_exc(
            code="subscription_required",
            message="Сценарии «что если» доступны на тарифе Pro.",
        )
    return user


def require_export(user: User = Depends(get_current_user)) -> User:
    if not limits_for(user).can_export:
        raise _upgrade_exc(
            code="subscription_required",
            message="Экспорт данных доступен на тарифе Pro.",
        )
    return user


async def enforce_snapshot_quota(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Block creating more snapshots than the plan allows this month."""
    limits = limits_for(user)
    if limits.max_snapshots_per_month is None:
        return user
    used = await _count_snapshots_this_month(db, user.id)
    if used >= limits.max_snapshots_per_month:
        raise _upgrade_exc(
            code="quota_exceeded",
            message=(
                f"Достигнут лимит снимков на тарифе {limits.label} "
                f"({limits.max_snapshots_per_month}/мес)."
            ),
        )
    return user


async def enforce_advice_quota(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Block generating more advice sessions than the plan allows this week."""
    limits = limits_for(user)
    if limits.advice_per_week is None:
        return user
    used = await _count_advice_this_week(db, user.id)
    if used >= limits.advice_per_week:
        raise _upgrade_exc(
            code="quota_exceeded",
            message=(
                f"Достигнут лимит советов на тарифе {limits.label} "
                f"({limits.advice_per_week}/нед)."
            ),
        )
    return user


def check_asset_count(limits: PlanLimits, count: int) -> None:
    """Raise if a snapshot has more assets than the plan permits."""
    if limits.max_assets_per_snapshot is not None and count > limits.max_assets_per_snapshot:
        raise _upgrade_exc(
            code="quota_exceeded",
            message=(
                f"Тариф {limits.label} допускает до "
                f"{limits.max_assets_per_snapshot} активов в снимке."
            ),
        )
