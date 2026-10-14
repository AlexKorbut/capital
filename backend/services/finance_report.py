"""Finance project report and category hints (fin-01).

All sums are Python ``Decimal`` over the stored per-expense values -- the report
never re-prices anything, so it is stable regardless of today's rates. Expenses
without a rate are counted (``unpriced_*``) but contribute nothing to a total.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.finance import FinExpense, FinProject
from schemas.finance import dec_str
from services.finance import as_uuid

# Suggested when the user has no history of their own (order is the display order).
STANDARD_CATEGORIES = (
    "топливо", "ремонт", "страховка", "налоги", "парковка", "мойка", "запчасти", "прочее",
)

_ZERO = Decimal("0.00")


@dataclass
class _Bucket:
    """Running totals for one group of expenses."""

    total: Decimal = _ZERO
    amount: Decimal = _ZERO  # sum in the original currency (meaningful per currency)
    count: int = 0
    unpriced: int = 0

    def add(self, amount: Decimal, base_amount: Decimal | None) -> None:
        self.amount += amount
        self.count += 1
        if base_amount is None:
            self.unpriced += 1
        else:
            self.total += base_amount


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _month(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _totals(buckets: dict[Any, _Bucket], key_name: str, key_fn=lambda k: k) -> list[dict]:
    return [
        {
            key_name: key_fn(key),
            "total_base": dec_str(b.total),
            "count": b.count,
            "unpriced_count": b.unpriced,
        }
        for key, b in buckets.items()
    ]


async def build_report(
    db: AsyncSession, project: FinProject, date_from: date | None, date_to: date | None
) -> dict[str, Any]:
    conds = [FinExpense.project_id == project.id, FinExpense.user_id == project.user_id]
    if date_from is not None:
        conds.append(FinExpense.spent_on >= date_from)
    if date_to is not None:
        conds.append(FinExpense.spent_on <= date_to)
    rows = (
        await db.execute(
            select(
                FinExpense.currency,
                FinExpense.amount,
                FinExpense.base_amount,
                FinExpense.spent_on,
                FinExpense.category,
            ).where(*conds)
        )
    ).all()

    overall = _Bucket()
    by_category: dict[str | None, _Bucket] = defaultdict(_Bucket)
    by_month: dict[str, _Bucket] = defaultdict(_Bucket)
    by_currency: dict[str, _Bucket] = defaultdict(_Bucket)
    unpriced_by_currency: dict[str, _Bucket] = defaultdict(_Bucket)
    for currency, amount, base_amount, spent_on, category in rows:
        for bucket in (overall, by_category[category], by_month[_month(spent_on)], by_currency[currency]):
            bucket.add(amount, base_amount)
        if base_amount is None:
            unpriced_by_currency[currency].add(amount, None)

    categories = _totals(by_category, "category")
    categories.sort(key=lambda r: (-Decimal(r["total_base"]), r["category"] is None, r["category"] or ""))
    months = sorted(_totals(by_month, "month"), key=lambda r: r["month"])
    currencies = [
        {**row, "amount": dec_str(by_currency[row["currency"]].amount)}
        for row in _totals(by_currency, "currency")
    ]
    currencies.sort(key=lambda r: (-Decimal(r["total_base"]), r["currency"]))
    unpriced = [
        {"currency": ccy, "count": b.count, "amount": dec_str(b.amount)}
        for ccy, b in unpriced_by_currency.items()
    ]
    unpriced.sort(key=lambda r: (-r["count"], r["currency"]))

    return {
        "project": {
            "id": str(project.id),
            "name": project.name,
            "base_currency": project.base_currency,
            "status": project.status,
            "started_on": _iso(project.started_on),
        },
        "period": {"from": _iso(date_from), "to": _iso(date_to)},
        "base_currency": project.base_currency,
        "total_base": dec_str(overall.total),
        "expenses_count": overall.count,
        "unpriced_count": overall.unpriced,
        "unpriced_by_currency": unpriced,
        "by_category": categories,
        "by_month": months,
        "by_currency": currencies,
    }


async def category_hints(db: AsyncSession, user_id: Any) -> list[dict[str, Any]]:
    """The user's own categories by frequency, then the standard ones not yet used."""
    rows = (
        await db.execute(
            select(FinExpense.category, func.count())
            .where(FinExpense.user_id == as_uuid(user_id), FinExpense.category.is_not(None))
            .group_by(FinExpense.category)
        )
    ).all()
    own = sorted(((name, int(count)) for name, count in rows), key=lambda r: (-r[1], r[0]))
    used = {name for name, _ in own}
    hints = [{"name": name, "count": count} for name, count in own]
    hints += [{"name": name, "count": 0} for name in STANDARD_CATEGORIES if name not in used]
    return hints
