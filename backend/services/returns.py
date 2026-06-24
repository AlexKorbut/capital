"""Net-worth growth (Feature #1).

We only persist net-worth *snapshots* (the user re-states their whole portfolio),
never individual cash flows — so a true investment XIRR (which must separate
contributions from gains) is not computable and we deliberately do NOT claim one.

What we DO compute, honestly labelled as net-worth *change*:
  - absolute + percentage change over rolling windows (7d / 30d / 90d / 1y / all);
  - CAGR (compound annual growth rate) across the full tracked span.

For each window we pick the most recent snapshot at or before (now − window) as
the baseline. If the user has no snapshot that old, we fall back to the earliest
snapshot and flag the window ``partial`` so the UI can show "(с начала)".
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.snapshot import Snapshot


def _to_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _aware(dt: datetime) -> datetime:
    """Normalise to timezone-aware UTC so naive/aware mixes never crash."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# (key, label, days). days=None -> all-time (baseline = first snapshot).
_WINDOWS: list[tuple[str, str, int | None]] = [
    ("7d", "7 дней", 7),
    ("30d", "30 дней", 30),
    ("90d", "90 дней", 90),
    ("1y", "1 год", 365),
    ("all", "Всё время", None),
]


def _pct(curr: Decimal, base: Decimal) -> str | None:
    # A meaningful percent change requires a positive baseline; a non-positive
    # baseline (zero or net debt) has no sensible denominator.
    if base <= 0:
        return None
    return str(((curr - base) / base * Decimal(100)).quantize(Decimal("0.1")))


async def compute_returns(db: AsyncSession, user_id) -> dict | None:
    rows = list(
        await db.scalars(
            select(Snapshot)
            .where(
                Snapshot.user_id == _to_uuid(user_id),
                Snapshot.is_confirmed.is_(True),
                Snapshot.total_usd.is_not(None),
            )
            .order_by(Snapshot.created_at.asc())
        )
    )
    if not rows:
        return None

    points = [(_aware(s.created_at), s.total_usd) for s in rows if s.created_at]
    if not points:
        return None

    now, current = points[-1][0], points[-1][1]
    first_date, first_val = points[0]

    windows: list[dict] = []
    for key, label, days in _WINDOWS:
        if days is None:
            base_date, base_val, partial = first_date, first_val, False
        else:
            threshold = now - timedelta(days=days)
            older = [(d, v) for d, v in points if d <= threshold]
            if older:
                base_date, base_val = older[-1]
                partial = False
            else:
                # No snapshot that old yet → show change since tracking began.
                base_date, base_val = first_date, first_val
                partial = True
        # Skip a window whose baseline is literally the current point (no span).
        if base_date == now and key != "all":
            continue
        change = (current - base_val).quantize(Decimal("0.01"))
        windows.append(
            {
                "key": key,
                "label": label,
                "baseline_usd": str(base_val),
                "baseline_date": base_date.isoformat(),
                "change_usd": str(change),
                "change_pct": _pct(current, base_val),
                "partial": partial,
            }
        )

    # CAGR across the full span (needs a meaningful span and positive endpoints).
    cagr_pct: str | None = None
    span_days = (now - first_date).days
    if span_days >= 30 and first_val > 0 and current > 0:
        ratio = float(current) / float(first_val)
        years = span_days / 365.0
        cagr = (ratio ** (1.0 / years) - 1.0) * 100.0
        cagr_pct = f"{cagr:.1f}"

    return {
        "current_usd": str(current),
        "as_of": now.isoformat(),
        "snapshots_count": len(points),
        "span_days": span_days,
        "first_date": first_date.isoformat(),
        "cagr_pct": cagr_pct,
        "windows": windows,
    }
