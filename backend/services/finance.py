"""Finance projects service (fin-01): projects, expenses, pricing and re-pricing.

An expense is stored in its own currency *and* in the project's base currency at
the rate of its date (``services.fx_history``). "No rate" is a normal state, not
an error: ``base_amount`` stays NULL and ``revalue_project`` fills it in later.

Ordering rule: finish every rate lookup, then ``Pricer.flush()`` (stores the rates
fetched along the way in THIS session), and only then change expenses and commit --
rates and expenses land in one transaction; on SQLite no write lock is held while a
lookup is still running.

Receipt photos (fin-02) hang off expenses: deleting an expense or a project drops the
attachment rows in the same transaction and removes the files only AFTER the commit
(``core.storage.remove_files``: a file that will not go is a log line, never a 500).

Nothing here logs amounts, notes or project names.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core import quotas
from core.storage import StorageBackend, remove_files, remove_tree
from models.finance import FinAttachment, FinExpense, FinProject
from models.user import User
from schemas.finance import ISO_4217
from services import fx_history
from services.fx_history import RATE_QUANT, FxBudget, RateQuote, apply_rate

# fin_expenses.base_amount is Numeric(20, 2): 18 integer digits.
MAX_BASE_AMOUNT = Decimal(10) ** 18


class FinanceError(Exception):
    """A business-rule failure the router turns into an HTTP error."""

    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFound(FinanceError):
    status_code = 404


class Conflict(FinanceError):
    status_code = 409


class Unprocessable(FinanceError):
    status_code = 422


class PayloadTooLarge(FinanceError):
    status_code = 413


class UnsupportedMedia(FinanceError):
    status_code = 415


class StorageUnavailable(FinanceError):
    status_code = 503


class AttachmentCorrupted(FinanceError):
    status_code = 500


class RequestTimeout(FinanceError):
    status_code = 408


class ClientGone(FinanceError):
    """The client hung up mid-request: nobody reads the answer, and it is not a server fault."""

    status_code = 499


def as_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


# --- pricing ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Pricing:
    base_amount: Decimal | None
    fx_rate: Decimal | None
    fx_date: date | None
    fx_source: str | None


UNPRICED = Pricing(None, None, None, None)


def _priced(amount: Decimal, rate: Decimal, fx_date: date | None, source: str | None) -> Pricing:
    base_amount = apply_rate(amount, rate)
    if abs(base_amount) >= MAX_BASE_AMOUNT:
        raise Unprocessable("Сумма в валюте проекта слишком велика")
    return Pricing(base_amount, rate, fx_date, source)


class Pricer:
    """Prices expenses for ONE API call: a shared FX time budget and memoised lookups."""

    def __init__(
        self, db: AsyncSession, budget: FxBudget | None = None, retry_no_data: bool = False
    ) -> None:
        self._db = db
        self._budget = FxBudget() if budget is None else budget
        self._retry_no_data = retry_no_data
        self._memo: dict[tuple[str, str, date], RateQuote | None] = {}

    async def rate(self, currency: str, base: str, on: date) -> RateQuote | None:
        key = (currency, base, on)
        if key not in self._memo:
            self._memo[key] = await fx_history.rate_between(
                self._db, currency, base, on, budget=self._budget, retry_no_data=self._retry_no_data
            )
        return self._memo[key]

    async def flush(self) -> None:
        """Store the rates fetched so far in the request's own session (no commit)."""
        await fx_history.flush_rates(self._db)

    async def price(
        self,
        *,
        amount: Decimal,
        currency: str,
        spent_on: date,
        base: str,
        manual_rate: Decimal | None = None,
    ) -> Pricing:
        if currency == base:  # nothing to convert; a manual rate would be meaningless
            return _priced(amount, Decimal(1), None, None)
        if manual_rate is not None:
            rate = manual_rate.quantize(RATE_QUANT, rounding=ROUND_HALF_UP)
            return _priced(amount, rate, None, "manual")
        quote = await self.rate(currency, base, spent_on)
        if quote is None:
            return UNPRICED
        return _priced(amount, quote.rate, quote.rate_date, quote.source)


def _apply_pricing(expense: FinExpense, pricing: Pricing) -> None:
    expense.base_amount = pricing.base_amount
    expense.fx_rate = pricing.fx_rate
    expense.fx_date = pricing.fx_date
    expense.fx_source = pricing.fx_source


# --- projects --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectStats:
    total_base: Decimal = Decimal("0.00")
    expenses_count: int = 0
    last_spent_on: date | None = None
    unpriced_count: int = 0


EMPTY_STATS = ProjectStats()


async def project_stats(
    db: AsyncSession, user_id: Any, project_ids: list[uuid.UUID] | None = None
) -> dict[uuid.UUID, ProjectStats]:
    """Totals per project, summed in Python ``Decimal`` (no float drift)."""
    stmt = select(FinExpense.project_id, FinExpense.base_amount, FinExpense.spent_on).where(
        FinExpense.user_id == as_uuid(user_id)
    )
    if project_ids is not None:
        if not project_ids:
            return {}
        stmt = stmt.where(FinExpense.project_id.in_(project_ids))

    acc: dict[uuid.UUID, ProjectStats] = {}
    for project_id, base_amount, spent_on in (await db.execute(stmt)).all():
        cur = acc.get(project_id, EMPTY_STATS)
        acc[project_id] = ProjectStats(
            total_base=cur.total_base + (base_amount if base_amount is not None else Decimal(0)),
            expenses_count=cur.expenses_count + 1,
            last_spent_on=max(filter(None, (cur.last_spent_on, spent_on))),
            unpriced_count=cur.unpriced_count + (1 if base_amount is None else 0),
        )
    return acc


async def stats_for(db: AsyncSession, project: FinProject) -> ProjectStats:
    return (await project_stats(db, project.user_id, [project.id])).get(project.id, EMPTY_STATS)


async def get_project(db: AsyncSession, user_id: Any, project_id: uuid.UUID) -> FinProject:
    project = await db.scalar(
        select(FinProject).where(FinProject.id == project_id, FinProject.user_id == as_uuid(user_id))
    )
    if project is None:
        raise NotFound("Проект не найден")
    return project


async def list_projects(
    db: AsyncSession, user_id: Any, status: str | None
) -> list[tuple[FinProject, ProjectStats]]:
    stmt = select(FinProject).where(FinProject.user_id == as_uuid(user_id))
    if status is not None:
        stmt = stmt.where(FinProject.status == status)
    projects = list(await db.scalars(stmt))
    stats = await project_stats(db, user_id, [p.id for p in projects])
    projects.sort(key=lambda p: (p.status != "active", p.name.casefold()))
    return [(p, stats.get(p.id, EMPTY_STATS)) for p in projects]


async def _name_taken(
    db: AsyncSession, user_id: uuid.UUID, name: str, exclude_id: uuid.UUID | None = None
) -> bool:
    stmt = select(FinProject.id).where(FinProject.user_id == user_id, FinProject.name == name)
    if exclude_id is not None:
        stmt = stmt.where(FinProject.id != exclude_id)
    return (await db.scalar(stmt.limit(1))) is not None


async def _commit_or_conflict(db: AsyncSession, message: str) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict(message) from exc


async def create_project(
    db: AsyncSession,
    user: User,
    *,
    name: str,
    base_currency: str | None,
    started_on: date | None,
    note: str | None,
) -> FinProject:
    user_id = user.id
    own_base = (user.base_currency or "").upper()
    base = base_currency or (own_base if own_base in ISO_4217 else "USD")
    if await _name_taken(db, user_id, name):
        raise Conflict("Проект с таким названием уже есть")
    project = FinProject(
        user_id=user_id, name=name, base_currency=base, started_on=started_on, note=note
    )
    db.add(project)
    await _commit_or_conflict(db, "Проект с таким названием уже есть")
    await db.refresh(project)
    return project


async def _reprice_all(
    db: AsyncSession, project: FinProject, new_base: str
) -> list[tuple[FinExpense, Pricing]]:
    """Re-price every expense of the project in ``new_base`` (no writes yet).

    A manual rate was stated in the old base currency, so it is carried over by
    converting it through the old->new rate of that day (it stays "manual").
    """
    old_base = project.base_currency
    expenses = list(await db.scalars(select(FinExpense).where(FinExpense.project_id == project.id)))
    pricer = Pricer(db)
    out: list[tuple[FinExpense, Pricing]] = []
    for e in expenses:
        manual: Decimal | None = None
        if e.fx_source == "manual" and e.fx_rate is not None and e.currency != new_base:
            cross = await pricer.rate(old_base, new_base, e.spent_on)
            if cross is None:
                out.append((e, UNPRICED))
                continue
            manual = e.fx_rate * cross.rate
        pricing = await pricer.price(
            amount=e.amount,
            currency=e.currency,
            spent_on=e.spent_on,
            base=new_base,
            manual_rate=manual,
        )
        out.append((e, pricing))
    await pricer.flush()
    return out


async def update_project(
    db: AsyncSession, project: FinProject, changes: dict[str, Any]
) -> FinProject:
    project_id, user_id = project.id, project.user_id
    new_name = changes.get("name")
    if new_name is not None and new_name != project.name:
        if await _name_taken(db, user_id, new_name, exclude_id=project_id):
            raise Conflict("Проект с таким названием уже есть")

    repriced: list[tuple[FinExpense, Pricing]] = []
    new_base = changes.get("base_currency")
    if new_base is not None and new_base != project.base_currency:
        repriced = await _reprice_all(db, project, new_base)  # may raise: nothing written yet

    for field in ("name", "base_currency", "status", "started_on", "note"):
        if field in changes:
            setattr(project, field, changes[field])
    for expense, pricing in repriced:
        _apply_pricing(expense, pricing)
    await _commit_or_conflict(db, "Проект с таким названием уже есть")
    await db.refresh(project)
    return project


async def discard_files(storage: StorageBackend, keys: list[str]) -> None:
    """Remove files off the event loop (a user can have hundreds); never raises."""
    if keys:
        await asyncio.to_thread(remove_files, storage, keys)


async def discard_tree(storage: StorageBackend, prefix: str) -> None:
    """Remove a whole folder (a user's) off the event loop; never raises."""
    await asyncio.to_thread(remove_tree, storage, prefix)


async def delete_attachment_rows(db: AsyncSession, *conditions: Any) -> list[str]:
    """Delete the attachment rows matching ``conditions`` (same transaction, no commit) and
    return their storage keys: the caller removes the files once its commit has succeeded.

    Explicit, because SQLite does not enforce the FK cascade here and Postgres refuses to drop
    an expense that still has attachments.
    """
    keys = list(await db.scalars(select(FinAttachment.storage_key).where(*conditions)))
    await db.execute(delete(FinAttachment).where(*conditions))
    return keys


async def delete_project(db: AsyncSession, project: FinProject, storage: StorageBackend) -> None:
    """Cascade by hand: SQLite does not enforce the FK here."""
    owned_expenses = select(FinExpense.id).where(
        FinExpense.project_id == project.id, FinExpense.user_id == project.user_id
    )
    keys = await delete_attachment_rows(
        db,
        FinAttachment.user_id == project.user_id,
        FinAttachment.expense_id.in_(owned_expenses),
    )
    await db.execute(
        delete(FinExpense).where(
            FinExpense.project_id == project.id, FinExpense.user_id == project.user_id
        )
    )
    await db.execute(
        delete(FinProject).where(FinProject.id == project.id, FinProject.user_id == project.user_id)
    )
    await db.commit()
    await discard_files(storage, keys)


# --- expenses --------------------------------------------------------------------------


async def get_expense(db: AsyncSession, user_id: Any, expense_id: uuid.UUID) -> FinExpense:
    expense = await db.scalar(
        select(FinExpense).where(FinExpense.id == expense_id, FinExpense.user_id == as_uuid(user_id))
    )
    if expense is None:
        raise NotFound("Трата не найдена")
    return expense


async def base_currency_of(db: AsyncSession, expense: FinExpense) -> str:
    base = await db.scalar(select(FinProject.base_currency).where(FinProject.id == expense.project_id))
    return base or "USD"


async def list_expenses(
    db: AsyncSession,
    user_id: Any,
    project_id: uuid.UUID,
    *,
    date_from: date | None,
    date_to: date | None,
    category: str | None,
    currency: str | None,
    limit: int,
    offset: int,
) -> tuple[list[FinExpense], int]:
    conds = [FinExpense.project_id == project_id, FinExpense.user_id == as_uuid(user_id)]
    if date_from is not None:
        conds.append(FinExpense.spent_on >= date_from)
    if date_to is not None:
        conds.append(FinExpense.spent_on <= date_to)
    if category is not None:
        conds.append(FinExpense.category == category)
    if currency is not None:
        conds.append(FinExpense.currency == currency)
    total = await db.scalar(select(func.count()).select_from(FinExpense).where(*conds))
    rows = await db.scalars(
        select(FinExpense)
        .where(*conds)
        .order_by(FinExpense.spent_on.desc(), FinExpense.created_at.desc(), FinExpense.id)
        .limit(limit)
        .offset(offset)
    )
    return list(rows), int(total or 0)


async def _find_by_client_id(
    db: AsyncSession, user_id: uuid.UUID, client_id: uuid.UUID
) -> FinExpense | None:
    return await db.scalar(
        select(FinExpense).where(FinExpense.user_id == user_id, FinExpense.client_id == client_id)
    )


def _same_project_or_conflict(existing: FinExpense, project_id: uuid.UUID) -> FinExpense:
    if existing.project_id != project_id:
        raise Conflict("Этот client_id уже использован для траты в другом проекте")
    return existing


async def _check_expense_ceiling(db: AsyncSession, user_id: uuid.UUID) -> None:
    """A retry of an existing ``client_id`` never gets here; only a genuinely new expense does."""
    held = await db.scalar(
        select(func.count()).select_from(FinExpense).where(FinExpense.user_id == user_id)
    )
    if (held or 0) >= quotas.MAX_USER_EXPENSES:
        limit = f"{quotas.MAX_USER_EXPENSES:,}".replace(",", " ")
        raise Conflict(f"Достигнут предел: не больше {limit} трат на пользователя")


async def create_expense(
    db: AsyncSession,
    project: FinProject,
    *,
    amount: Decimal,
    currency: str,
    spent_on: date,
    category: str | None,
    note: str | None,
    fx_rate: Decimal | None,
    client_id: uuid.UUID | None,
) -> tuple[FinExpense, bool]:
    """Returns ``(expense, created)``; a repeated ``client_id`` returns the original."""
    user_id, project_id, base = project.user_id, project.id, project.base_currency
    if client_id is not None:
        existing = await _find_by_client_id(db, user_id, client_id)
        if existing is not None:
            return _same_project_or_conflict(existing, project_id), False

    await _check_expense_ceiling(db, user_id)
    pricer = Pricer(db)
    pricing = await pricer.price(
        amount=amount, currency=currency, spent_on=spent_on, base=base, manual_rate=fx_rate
    )
    await pricer.flush()
    expense = FinExpense(
        user_id=user_id,
        project_id=project_id,
        amount=amount,
        currency=currency,
        spent_on=spent_on,
        category=category,
        note=note,
        client_id=client_id,
    )
    _apply_pricing(expense, pricing)
    db.add(expense)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if client_id is None:
            raise
        winner = await _find_by_client_id(db, user_id, client_id)  # lost a race: same key
        if winner is None:
            raise
        return _same_project_or_conflict(winner, project_id), False
    await db.refresh(expense)
    return expense, True


async def update_expense(
    db: AsyncSession, expense: FinExpense, base: str, changes: dict[str, Any]
) -> FinExpense:
    amount = changes.get("amount", expense.amount)
    currency = changes.get("currency", expense.currency)
    spent_on = changes.get("spent_on", expense.spent_on)
    currency_changed = currency != expense.currency
    date_changed = spent_on != expense.spent_on
    amount_changed = amount != expense.amount
    rate_given = "fx_rate" in changes

    pricing: Pricing | None = None
    if rate_given or currency_changed or date_changed or amount_changed:
        was_manual = expense.fx_source == "manual" and expense.fx_rate is not None
        manual = changes["fx_rate"] if rate_given else None
        if not rate_given and was_manual and not currency_changed:
            manual = expense.fx_rate  # an edit of amount/date keeps the owner's own rate
        amount_only = not (rate_given or currency_changed or date_changed)
        if amount_only and expense.fx_rate is not None and not was_manual:
            pricing = _priced(amount, expense.fx_rate, expense.fx_date, expense.fx_source)
        else:
            # An edit is the user asking for a (new) price: ignore a remembered "no data".
            pricer = Pricer(db, retry_no_data=True)
            pricing = await pricer.price(
                amount=amount, currency=currency, spent_on=spent_on, base=base, manual_rate=manual
            )
            await pricer.flush()

    for field in ("amount", "currency", "spent_on", "category", "note"):
        if field in changes:
            setattr(expense, field, changes[field])
    if pricing is not None:
        _apply_pricing(expense, pricing)
    await db.commit()
    await db.refresh(expense)
    return expense


async def delete_expense(db: AsyncSession, expense: FinExpense, storage: StorageBackend) -> None:
    keys = await delete_attachment_rows(
        db, FinAttachment.expense_id == expense.id, FinAttachment.user_id == expense.user_id
    )
    await db.execute(
        delete(FinExpense).where(FinExpense.id == expense.id, FinExpense.user_id == expense.user_id)
    )
    await db.commit()
    await discard_files(storage, keys)


async def revalue_project(db: AsyncSession, project: FinProject) -> tuple[int, int]:
    """Price the expenses that have no rate yet. Returns ``(revalued, still_unpriced)``.

    The write is ``UPDATE .. WHERE base_amount IS NULL``: an expense that got a price in
    the meantime (a manual edit while the rates were being fetched) is left alone, and
    ``revalued`` counts only the rows this call actually changed.
    """
    project_id, user_id, base = project.id, project.user_id, project.base_currency
    unpriced = list(
        await db.scalars(
            select(FinExpense)
            .where(
                FinExpense.project_id == project_id,
                FinExpense.user_id == user_id,
                FinExpense.base_amount.is_(None),
            )
            .order_by(FinExpense.spent_on.desc())
        )
    )
    pricer = Pricer(db, retry_no_data=True)  # one time budget; an explicit "ask again"
    resolved: list[tuple[uuid.UUID, Pricing]] = []
    for e in unpriced:
        pricing = await pricer.price(
            amount=e.amount, currency=e.currency, spent_on=e.spent_on, base=base
        )
        if pricing.base_amount is not None:
            resolved.append((e.id, pricing))
    await pricer.flush()

    revalued = 0
    for expense_id, pricing in resolved:
        result = await db.execute(
            update(FinExpense)
            .where(
                FinExpense.id == expense_id,
                FinExpense.user_id == user_id,
                FinExpense.base_amount.is_(None),
            )
            .values(
                base_amount=pricing.base_amount,
                fx_rate=pricing.fx_rate,
                fx_date=pricing.fx_date,
                fx_source=pricing.fx_source,
            )
            .execution_options(synchronize_session=False)
        )
        revalued += result.rowcount or 0
    await db.commit()
    still_unpriced = await db.scalar(
        select(func.count())
        .select_from(FinExpense)
        .where(
            FinExpense.project_id == project_id,
            FinExpense.user_id == user_id,
            FinExpense.base_amount.is_(None),
        )
    )
    return revalued, int(still_unpriced or 0)
