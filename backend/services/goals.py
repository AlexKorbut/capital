"""Goals service (Feature #5) — progress + naive date projection.

Progress = current net worth / target. The projected achievement date is a
simple linear extrapolation from recent net-worth growth (last ~90 days, or the
full span if shorter). It is explicitly a naive estimate, not a forecast.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.goal import Goal
from services import portfolio_read, returns as returns_service


def _to_uuid(v) -> uuid.UUID:
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


async def _current_net_worth(db: AsyncSession, user_id) -> Decimal | None:
    snap = await portfolio_read.latest_snapshot(db, user_id)
    return snap.total_usd if snap and snap.total_usd is not None else None


async def _monthly_growth_usd(db: AsyncSession, user_id) -> Decimal | None:
    """Average $/month net-worth change from recent history (None if flat/declining)."""
    data = await returns_service.compute_returns(db, user_id)
    if not data:
        return None
    windows = {w["key"]: w for w in data["windows"]}
    # Prefer 90d, else 30d, else all-time.
    for key, months in (("90d", 3), ("30d", 1), ("1y", 12), ("all", None)):
        w = windows.get(key)
        if not w or w.get("change_usd") is None:
            continue
        change = Decimal(w["change_usd"])
        if months is None:
            span_days = max(int(data.get("span_days") or 0), 1)
            months = max(span_days / 30.0, 0.1)
        rate = change / Decimal(str(months))
        return rate if rate > 0 else None
    return None


async def list_goals_with_progress(db: AsyncSession, user_id) -> list[dict]:
    rows = list(
        await db.scalars(
            select(Goal).where(Goal.user_id == _to_uuid(user_id)).order_by(Goal.created_at.asc())
        )
    )
    if not rows:
        return []

    current = await _current_net_worth(db, user_id) or Decimal(0)
    monthly = await _monthly_growth_usd(db, user_id)

    out: list[dict] = []
    for g in rows:
        target = g.target_usd
        achieved = current >= target and target > 0
        pct = None
        if target and target > 0:
            pct = (current / target * Decimal(100)).quantize(Decimal("0.1"))
            if pct > Decimal(100):
                pct = Decimal(100)
        remaining = (target - current) if target > current else Decimal(0)

        projected_date: str | None = None
        if not achieved and remaining > 0 and monthly and monthly > 0:
            months_needed = float(remaining / monthly)
            if months_needed < 1200:  # cap absurd projections (>100y)
                projected_date = (
                    date.today() + timedelta(days=int(months_needed * 30.4))
                ).isoformat()

        out.append(
            {
                "id": str(g.id),
                "title": g.title,
                "target_usd": str(target),
                "target_date": g.target_date.isoformat() if g.target_date else None,
                "current_usd": str(current),
                "remaining_usd": str(remaining),
                "progress_pct": str(pct) if pct is not None else None,
                "achieved": achieved,
                "monthly_growth_usd": str(monthly) if monthly else None,
                "projected_date": projected_date,
                "created_at": g.created_at.isoformat() if g.created_at else None,
            }
        )
    return out


async def create_goal(
    db: AsyncSession, user_id, title: str, target_usd: Decimal, target_date: date | None
) -> Goal:
    goal = Goal(
        user_id=_to_uuid(user_id),
        title=title.strip()[:120] or "Цель",
        target_usd=target_usd,
        target_date=target_date,
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    return goal


async def delete_goal(db: AsyncSession, user_id, goal_id) -> bool:
    goal = await db.get(Goal, _to_uuid(goal_id))
    if goal is None or goal.user_id != _to_uuid(user_id):
        return False
    await db.delete(goal)
    await db.commit()
    return True
