"""Portfolio router — input + confirm (Срез 1).

Flow:
    POST /portfolio/input    free-form text -> parse/validate/enrich,
                             graph pauses at human_review, returns a PREVIEW.
    POST /portfolio/confirm  apply optional human edits -> resume -> persist.

The graph is durable (checkpointer), so `confirm` may land in a different
process than `input` and still resume the same thread.

NOTE: no ``from __future__ import annotations`` here on purpose. ``/input`` is
``@limiter.limit``-decorated; slowapi's ``functools.wraps`` wrapper would otherwise
expose stringized annotations that FastAPI resolves against slowapi's globals,
mis-reading the ``InputRequest`` body as a query param (422). Eager annotations fix it.
"""
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from agents import runners
from agents.state import AssetItem
from core.db import get_db
from core.deps import get_current_user
from core.ratelimit import INPUT_LIMIT, limiter
from core.tiers import check_asset_count, enforce_snapshot_quota, limits_for
from models.user import User
from schemas.portfolio import (
    AllocationResponse,
    AllocationUpdate,
    AssetUpsert,
    ConfirmRequest,
    ConfirmResponse,
    CurrentPortfolio,
    HistoryPoint,
    InputRequest,
    PreviewResponse,
    ReturnsResponse,
    SnapshotSummary,
)
from services import allocation as allocation_service
from services import portfolio_edit, portfolio_read, returns as returns_service

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

# Hard cap on uploaded file size: xlsx goes to pandas/openpyxl, so an unbounded
# read is a zip-bomb / OOM DoS vector. Reject oversized payloads up front.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _graphs(request: Request) -> runners.GraphRegistry:
    graphs = getattr(request.app.state, "graphs", None)
    if graphs is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Graph runtime not ready",
        )
    return graphs


def _as_asset_items(raw: Any) -> list[AssetItem]:
    """Coerce graph state's `assets` (AssetItem or dict) into AssetItem list."""
    items: list[AssetItem] = []
    for a in raw or []:
        items.append(a if isinstance(a, AssetItem) else AssetItem.model_validate(a))
    return items


def _total_usd_str(state: dict[str, Any]) -> str | None:
    total = state.get("total_usd")
    return str(total) if total is not None else None


@router.post("/input", response_model=PreviewResponse)
@limiter.limit(INPUT_LIMIT)
async def portfolio_input(
    request: Request,
    body: InputRequest,
    current: User = Depends(get_current_user),
) -> PreviewResponse:
    graphs = _graphs(request)
    thread_id = str(uuid.uuid4())
    base_currency = (body.base_currency or current.base_currency or "USD").upper()

    state = {
        "user_id": str(current.id),
        "base_currency": base_currency,
        "input_type": body.input_type or "text",
        "raw_text": body.text,
    }
    result = await runners.run_input(graphs, thread_id, state)

    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    return PreviewResponse(
        thread_id=thread_id,
        assets=_as_asset_items(result.get("assets")),
        validation=result.get("validation"),
        needs_review=bool(result.get("needs_review")),
        total_usd=_total_usd_str(result),
    )


@router.post("/input/upload", response_model=PreviewResponse)
# NOTE: slowapi's wrapper breaks FastAPI forward-ref resolution for UploadFile/
# Form params, so we don't decorate this route. The JSON /input path is limited;
# uploads are still bounded by the per-plan snapshot quota at /confirm.
async def portfolio_input_upload(
    request: Request,
    file: UploadFile = File(...),
    input_type: str = Form("file"),
    base_currency: str | None = Form(None),
    current: User = Depends(get_current_user),
) -> PreviewResponse:
    """Voice/file/image input: decode -> parse -> validate -> enrich -> preview.

    `input_type` is "voice" (audio -> Whisper), "file" (xlsx/csv) or "image"
    (screenshot/photo -> vision). The graph routes on it; the rest of the flow
    (preview + /confirm) is identical to the text path.
    """
    graphs = _graphs(request)
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")

    thread_id = str(uuid.uuid4())
    ccy = (base_currency or current.base_currency or "USD").upper()
    if input_type not in ("voice", "file", "image"):
        input_type = "file"

    state = {
        "user_id": str(current.id),
        "base_currency": ccy,
        "input_type": input_type,
        "raw_text": "",
        "raw_bytes": raw,
        "mime_type": file.content_type,
        "filename": file.filename or "",
    }
    result = await runners.run_input(graphs, thread_id, state)

    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    return PreviewResponse(
        thread_id=thread_id,
        assets=_as_asset_items(result.get("assets")),
        validation=result.get("validation"),
        needs_review=bool(result.get("needs_review")),
        total_usd=_total_usd_str(result),
    )


@router.get("/import/template")
async def import_template(current: User = Depends(get_current_user)) -> Response:
    """Download a ready-to-edit CSV template for bulk import."""
    from services.importer import CSV_TEMPLATE

    return Response(
        content=CSV_TEMPLATE.encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="kapital-template.csv"'},
    )


@router.post("/import", response_model=PreviewResponse)
async def portfolio_import(
    request: Request,
    file: UploadFile = File(...),
    base_currency: str | None = Form(None),
    current: User = Depends(get_current_user),
) -> PreviewResponse:
    """Deterministic spreadsheet/CSV import (no LLM): table -> assets -> preview.

    Reuses the same graph (validate -> enrich -> human_review interrupt) and the
    standard /confirm flow, so imported assets are previewed and editable before
    they persist.
    """
    from services.importer import parse_table_bytes

    graphs = _graphs(request)
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")

    seeded = parse_table_bytes(raw, filename=file.filename or "")
    if not seeded:
        raise HTTPException(
            status_code=400,
            detail="Не удалось распознать таблицу. Проверьте заголовки колонок "
            "(тип, сумма, валюта…) или скачайте шаблон.",
        )

    thread_id = str(uuid.uuid4())
    ccy = (base_currency or current.base_currency or "USD").upper()
    state = {
        "user_id": str(current.id),
        "base_currency": ccy,
        "input_type": "import",
        "raw_text": "",
        "assets": seeded,
    }
    result = await runners.run_input(graphs, thread_id, state)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    return PreviewResponse(
        thread_id=thread_id,
        assets=_as_asset_items(result.get("assets")),
        validation=result.get("validation"),
        needs_review=bool(result.get("needs_review")),
        total_usd=_total_usd_str(result),
    )


@router.post("/confirm", response_model=ConfirmResponse)
async def portfolio_confirm(
    body: ConfirmRequest,
    request: Request,
    current: User = Depends(enforce_snapshot_quota),
) -> ConfirmResponse:
    graphs = _graphs(request)

    # Make sure this thread exists and belongs to the caller before resuming.
    pending = await runners.get_pending(graphs, body.thread_id)
    values = getattr(pending, "values", None) or {}
    if not values or values.get("user_id") != str(current.id):
        raise HTTPException(status_code=404, detail="No pending input for this thread")

    edits: dict[str, Any] | None = None
    if body.assets is not None:
        edits = {"assets": body.assets}

    # Plan gate: a snapshot may hold at most N assets (resume below persists it).
    final_assets = body.assets if body.assets is not None else values.get("assets")
    check_asset_count(limits_for(current), len(final_assets or []))

    result = await runners.resume_input(graphs, body.thread_id, edits)

    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    snapshot_id = result.get("snapshot_id")
    if not snapshot_id:
        raise HTTPException(status_code=500, detail="Persistence did not return a snapshot id")

    return ConfirmResponse(
        snapshot_id=snapshot_id,
        assets=_as_asset_items(result.get("assets")),
        total_usd=_total_usd_str(result),
    )


# --- Read side (dashboard) ----------------------------------------------------


@router.get("/current", response_model=CurrentPortfolio | None)
async def portfolio_current(
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CurrentPortfolio | None:
    data = await portfolio_read.current_portfolio(
        db, current.id, base_currency=current.base_currency or "USD"
    )
    return CurrentPortfolio.model_validate(data) if data else None


def _upsert_to_item(body: AssetUpsert) -> AssetItem:
    return AssetItem(
        asset_type=body.asset_type,
        amount=body.amount,
        currency=(body.currency or "USD").upper(),
        country=body.country,
        location=body.location,
        note=body.note,
        ticker=(body.ticker or None) and body.ticker.upper(),
        symbol=(body.symbol or None) and body.symbol.upper(),
        quantity=body.quantity,
        interest_rate=body.interest_rate,
        appreciation_rate=body.appreciation_rate,
        counterparty=body.counterparty,
        is_owed_to_me=body.is_owed_to_me,
        confidence=1.0,
    )


async def _current_or_404(db: AsyncSession, user) -> CurrentPortfolio:
    data = await portfolio_read.current_portfolio(
        db, user.id, base_currency=user.base_currency or "USD"
    )
    if not data:
        raise HTTPException(status_code=404, detail="Портфель пуст")
    return CurrentPortfolio.model_validate(data)


@router.post("/assets", response_model=CurrentPortfolio, status_code=status.HTTP_201_CREATED)
async def add_asset(
    body: AssetUpsert,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CurrentPortfolio:
    await portfolio_edit.add_asset(
        db, current.id, _upsert_to_item(body), base_currency=current.base_currency or "USD"
    )
    return await _current_or_404(db, current)


@router.patch("/assets/{asset_id}", response_model=CurrentPortfolio)
async def update_asset(
    asset_id: uuid.UUID,
    body: AssetUpsert,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CurrentPortfolio:
    ok = await portfolio_edit.update_asset(db, current.id, asset_id, _upsert_to_item(body))
    if not ok:
        raise HTTPException(status_code=404, detail="Актив не найден (или не в текущем снимке)")
    return await _current_or_404(db, current)


@router.delete("/assets/{asset_id}", response_model=CurrentPortfolio)
async def delete_asset(
    asset_id: uuid.UUID,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CurrentPortfolio:
    ok = await portfolio_edit.delete_asset(db, current.id, asset_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Актив не найден (или не в текущем снимке)")
    return await _current_or_404(db, current)


@router.get("/snapshots", response_model=list[SnapshotSummary])
async def portfolio_snapshots(
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SnapshotSummary]:
    rows = await portfolio_read.list_snapshots(db, current.id)
    return [SnapshotSummary.model_validate(r) for r in rows]


@router.get("/history/chart", response_model=list[HistoryPoint])
async def portfolio_history_chart(
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[HistoryPoint]:
    points = await portfolio_read.history_chart(db, current.id)
    return [HistoryPoint.model_validate(p) for p in points]


@router.get("/returns", response_model=ReturnsResponse | None)
async def portfolio_returns(
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReturnsResponse | None:
    data = await returns_service.compute_returns(db, current.id)
    return ReturnsResponse.model_validate(data) if data else None


@router.get("/allocation", response_model=AllocationResponse)
async def portfolio_allocation(
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AllocationResponse:
    data = await allocation_service.compute_allocation(db, current)
    return AllocationResponse.model_validate(data)


@router.put("/allocation", response_model=AllocationResponse)
async def set_portfolio_allocation(
    body: AllocationUpdate,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AllocationResponse:
    await allocation_service.set_targets(db, current, body.targets)
    data = await allocation_service.compute_allocation(db, current)
    return AllocationResponse.model_validate(data)
