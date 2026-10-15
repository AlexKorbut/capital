"""Finance projects router (fin-01) — projects, expenses in any currency, reports.

Every route is scoped to the caller (JWT or personal ``kp_`` token); someone else's
project or expense answers 404, never 403. Money travels as strings. Business
failures arrive as ``FinanceError`` and are rendered by the handler in ``main``.

Writes (POST/PATCH of projects and expenses), ``/fx`` and ``/revalue`` are rate-limited
per USER (``user_key``), not per IP: a host calls from one address for all its users.

NOTE: no ``from __future__ import annotations`` here on purpose. The limited routes
are ``@limiter.limit``-decorated; slowapi's ``functools.wraps`` wrapper
would otherwise expose stringized annotations that FastAPI resolves against slowapi's
globals and mis-read the parameters (see routers/portfolio.py).
"""
import uuid
from datetime import date
from typing import Annotated, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.deps import get_current_user
from core.ratelimit import (
    FINANCE_FX_LIMIT,
    FINANCE_REVALUE_LIMIT,
    FINANCE_WRITE_LIMIT,
    limiter,
    user_key,
)
from core.storage import StorageBackend, get_storage
from models.finance import FinAttachment, FinExpense, FinProject
from models.user import User
from schemas.finance import (
    AttachmentBrief,
    Category,
    CategoryHint,
    CurrencyCode,
    ExpenseCreate,
    ExpenseList,
    ExpenseOut,
    ExpenseUpdate,
    FxPreview,
    ISO_4217,
    ProjectCreate,
    ProjectOut,
    ProjectReport,
    ProjectStatus,
    ProjectUpdate,
    RevalueResult,
    SpentOn,
    dec_str,
)
from services import attachments as attachments_service
from services import finance as finance_service
from services import finance_report, fx_history

router = APIRouter(prefix="/finance", tags=["finance"])

DEFAULT_PAGE = 50
MAX_PAGE = 200


async def finance_user(request: Request, current: User = Depends(get_current_user)) -> User:
    """The authenticated user, also published as the rate-limit key of this request."""
    request.state.rate_key = f"user:{current.id}"
    return current


# --- response mapping ---------------------------------------------------------------------


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _project_out(project: FinProject, stats: finance_service.ProjectStats) -> ProjectOut:
    return ProjectOut(
        id=str(project.id),
        name=project.name,
        note=project.note,
        base_currency=project.base_currency,
        status=project.status,
        started_on=_iso(project.started_on),
        created_at=_iso(project.created_at),
        updated_at=_iso(project.updated_at),
        total_base=dec_str(stats.total_base),
        expenses_count=stats.expenses_count,
        last_spent_on=_iso(stats.last_spent_on),
        unpriced_count=stats.unpriced_count,
    )


def _attachment_brief(a: FinAttachment) -> AttachmentBrief:
    return AttachmentBrief(
        id=str(a.id), mime=a.mime, size_bytes=a.size_bytes, created_at=_iso(a.created_at)
    )


def _expense_out(
    e: FinExpense, base_currency: str, attachments: Sequence[FinAttachment] = ()
) -> ExpenseOut:
    return ExpenseOut(
        id=str(e.id),
        project_id=str(e.project_id),
        amount=dec_str(e.amount),
        currency=e.currency,
        spent_on=e.spent_on.isoformat(),
        category=e.category,
        note=e.note,
        base_amount=dec_str(e.base_amount),
        base_currency=base_currency,
        fx_rate=dec_str(e.fx_rate, min_places=0),
        fx_date=_iso(e.fx_date),
        fx_source=e.fx_source,
        client_id=str(e.client_id) if e.client_id else None,
        created_at=_iso(e.created_at),
        updated_at=_iso(e.updated_at),
        attachments=[_attachment_brief(a) for a in attachments],
    )


async def _expenses_out(
    db: AsyncSession, user_id: uuid.UUID, expenses: Sequence[FinExpense], base_currency: str
) -> list[ExpenseOut]:
    """Expenses with their receipt lists, fetched in one query for the whole page."""
    by_expense = await attachments_service.attachments_by_expense(
        db, user_id, [e.id for e in expenses]
    )
    return [_expense_out(e, base_currency, by_expense.get(e.id, ())) for e in expenses]


def _check_period(date_from: Optional[date], date_to: Optional[date]) -> None:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "from must not be after to")


# --- projects -------------------------------------------------------------------------------


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(
    status_filter: Annotated[Optional[ProjectStatus], Query(alias="status")] = None,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectOut]:
    rows = await finance_service.list_projects(db, current.id, status_filter)
    return [_project_out(project, stats) for project, stats in rows]


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
@limiter.limit(FINANCE_WRITE_LIMIT, key_func=user_key)
async def create_project(
    request: Request,
    body: ProjectCreate,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    project = await finance_service.create_project(
        db,
        current,
        name=body.name,
        base_currency=body.base_currency,
        started_on=body.started_on,
        note=body.note,
    )
    return _project_out(project, finance_service.EMPTY_STATS)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: uuid.UUID,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    project = await finance_service.get_project(db, current.id, project_id)
    return _project_out(project, await finance_service.stats_for(db, project))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
@limiter.limit(FINANCE_WRITE_LIMIT, key_func=user_key)
async def update_project(
    request: Request,
    project_id: uuid.UUID,
    body: ProjectUpdate,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    project = await finance_service.get_project(db, current.id, project_id)
    project = await finance_service.update_project(
        db, project, body.model_dump(exclude_unset=True)
    )
    return _project_out(project, await finance_service.stats_for(db, project))


@router.delete(
    "/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_project(
    project_id: uuid.UUID,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> Response:
    project = await finance_service.get_project(db, current.id, project_id)
    await finance_service.delete_project(db, project, storage)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- expenses -------------------------------------------------------------------------------


@router.get("/projects/{project_id}/expenses", response_model=ExpenseList)
async def list_expenses(
    project_id: uuid.UUID,
    date_from: Annotated[Optional[date], Query(alias="from")] = None,
    date_to: Annotated[Optional[date], Query(alias="to")] = None,
    category: Annotated[Category, Query()] = None,
    currency: Annotated[Optional[CurrencyCode], Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
) -> ExpenseList:
    _check_period(date_from, date_to)
    project = await finance_service.get_project(db, current.id, project_id)
    rows, total = await finance_service.list_expenses(
        db,
        current.id,
        project.id,
        date_from=date_from,
        date_to=date_to,
        category=category,
        currency=currency,
        limit=limit,
        offset=offset,
    )
    return ExpenseList(
        items=await _expenses_out(db, current.id, rows, project.base_currency),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/projects/{project_id}/expenses",
    response_model=ExpenseOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(FINANCE_WRITE_LIMIT, key_func=user_key)
async def create_expense(
    request: Request,
    project_id: uuid.UUID,
    body: ExpenseCreate,
    response: Response,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
) -> ExpenseOut:
    user_id = current.id  # read now: a lost client_id race rolls back and expires ``current``
    project = await finance_service.get_project(db, user_id, project_id)
    base_currency = project.base_currency
    expense, created = await finance_service.create_expense(
        db,
        project,
        amount=body.amount,
        currency=body.currency,
        spent_on=body.spent_on,
        category=body.category,
        note=body.note,
        fx_rate=body.fx_rate,
        client_id=body.client_id,
    )
    if not created:  # the same client_id came again: hand back the original, no duplicate
        response.status_code = status.HTTP_200_OK
    [out] = await _expenses_out(db, user_id, [expense], base_currency)
    return out


@router.patch("/expenses/{expense_id}", response_model=ExpenseOut)
@limiter.limit(FINANCE_WRITE_LIMIT, key_func=user_key)
async def update_expense(
    request: Request,
    expense_id: uuid.UUID,
    body: ExpenseUpdate,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
) -> ExpenseOut:
    user_id = current.id
    expense = await finance_service.get_expense(db, user_id, expense_id)
    base_currency = await finance_service.base_currency_of(db, expense)
    expense = await finance_service.update_expense(
        db, expense, base_currency, body.model_dump(exclude_unset=True)
    )
    [out] = await _expenses_out(db, user_id, [expense], base_currency)
    return out


@router.delete(
    "/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_expense(
    expense_id: uuid.UUID,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> Response:
    expense = await finance_service.get_expense(db, current.id, expense_id)
    await finance_service.delete_expense(db, expense, storage)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- report / revalue -----------------------------------------------------------------------


@router.get("/projects/{project_id}/report", response_model=ProjectReport)
async def project_report(
    project_id: uuid.UUID,
    date_from: Annotated[Optional[date], Query(alias="from")] = None,
    date_to: Annotated[Optional[date], Query(alias="to")] = None,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectReport:
    _check_period(date_from, date_to)
    project = await finance_service.get_project(db, current.id, project_id)
    return ProjectReport.model_validate(
        await finance_report.build_report(db, project, date_from, date_to)
    )


@router.post("/projects/{project_id}/revalue", response_model=RevalueResult)
@limiter.limit(FINANCE_REVALUE_LIMIT, key_func=user_key)
async def revalue_project(
    request: Request,
    project_id: uuid.UUID,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
) -> RevalueResult:
    project = await finance_service.get_project(db, current.id, project_id)
    revalued, still_unpriced = await finance_service.revalue_project(db, project)
    return RevalueResult(revalued=revalued, unpriced_count=still_unpriced)


# --- form helpers ---------------------------------------------------------------------------


@router.get("/fx", response_model=FxPreview)
@limiter.limit(FINANCE_FX_LIMIT, key_func=user_key)
async def fx_preview(
    request: Request,
    currency: Annotated[CurrencyCode, Query()],
    on: Annotated[Optional[SpentOn], Query(alias="date")] = None,
    base: Annotated[Optional[CurrencyCode], Query()] = None,
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
) -> FxPreview:
    """The rate the expense form would use: base-currency units per 1 unit of ``currency``."""
    day = on or fx_history.today_utc()
    own_base = (current.base_currency or "").upper()
    base_ccy = base or (own_base if own_base in ISO_4217 else "USD")
    quote = await fx_history.rate_between(db, currency, base_ccy, day)
    await fx_history.flush_rates(db)
    await db.commit()
    return FxPreview(
        currency=currency,
        base=base_ccy,
        date=day.isoformat(),
        rate=dec_str(quote.rate, min_places=0) if quote else None,
        # No lookup happened (same currency): there is no rate date to show.
        fx_date=_iso(quote.rate_date) if quote and quote.source else None,
        source=quote.source if quote else None,
    )


@router.get("/categories", response_model=list[CategoryHint])
async def category_hints(
    current: User = Depends(finance_user),
    db: AsyncSession = Depends(get_db),
) -> list[CategoryHint]:
    return [CategoryHint(**h) for h in await finance_report.category_hints(db, current.id)]
