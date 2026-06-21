"""Target allocation & drift (Feature #7).

The user sets target percentages per asset type; we compare them to the actual
mix from the latest snapshot. Purely descriptive — we report drift, never advise
buying or selling (consistent with the advisor guardrails). Targets live in
``user.settings["allocation"]`` (no migration needed).
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from services import portfolio_read

_TYPES = ["cash", "bank_deposit", "crypto", "stock", "real_estate", "debt", "other"]


def get_targets(user: User) -> dict[str, float]:
    raw = (user.settings or {}).get("allocation") or {}
    return {k: float(v) for k, v in raw.items() if k in _TYPES}


async def set_targets(db: AsyncSession, user: User, targets: dict[str, float]) -> dict[str, float]:
    clean = {
        k: round(float(v), 1)
        for k, v in targets.items()
        if k in _TYPES and float(v) > 0
    }
    settings_dict = dict(user.settings or {})
    settings_dict["allocation"] = clean
    user.settings = settings_dict
    await db.commit()
    return clean


async def compute_allocation(db: AsyncSession, user: User) -> dict:
    targets = get_targets(user)
    snap = await portfolio_read.latest_snapshot(db, user.id)

    current_pct: dict[str, Decimal] = {}
    if snap is not None:
        data = await portfolio_read.current_portfolio(db, user.id)
        by_type = (data or {}).get("breakdown", {}).get("by_type", [])
        # Use abs() so debts (negative usd_value) don't inflate other types
        # above 100% or yield negative shares; the signed value stays for display.
        total = sum(
            abs(Decimal(e["usd_value"]))
            for e in by_type
            if e.get("usd_value") is not None and Decimal(e["usd_value"]) != 0
        )
        if total and total > 0:
            for e in by_type:
                if e.get("usd_value") is not None and Decimal(e["usd_value"]) != 0:
                    current_pct[e["key"]] = (
                        abs(Decimal(e["usd_value"])) / total * Decimal(100)
                    ).quantize(Decimal("0.1"))

    keys = sorted(set(targets) | set(current_pct), key=lambda k: _TYPES.index(k) if k in _TYPES else 99)
    rows = []
    for k in keys:
        cur = current_pct.get(k, Decimal(0))
        tgt = Decimal(str(targets.get(k, 0)))
        rows.append(
            {
                "asset_type": k,
                "current_pct": str(cur),
                "target_pct": str(tgt) if k in targets else None,
                "drift_pct": str((cur - tgt).quantize(Decimal("0.1"))) if k in targets else None,
            }
        )
    return {"has_target": bool(targets), "rows": rows}
