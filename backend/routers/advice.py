"""Advice router — generate / fetch / mark-read + the portfolio WebSocket.

POST /advice/generate     run the advisor graph on the user's latest snapshot
GET  /advice/latest       most recent advice session + items
POST /advice/{id}/read    mark one advice item read
WS   /ws/portfolio        push channel (advice_ready, ...); token-authenticated
"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents import runners
from core.db import SessionLocal, get_db
from core.deps import get_current_user
from core.ratelimit import ADVICE_LIMIT, limiter
from core.security import decode_token
from core.tiers import enforce_advice_quota
from core.ws import get_ws_manager
from models.advice import AdviceItem, AdviceSession
from models.user import User
from schemas.advice import AdviceItemOut, AdviceSessionOut

router = APIRouter(tags=["advice"])


def _graphs(request: Request) -> runners.GraphRegistry:
    graphs = getattr(request.app.state, "graphs", None)
    if graphs is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Graph runtime not ready",
        )
    return graphs


def _item_out(i: AdviceItem) -> AdviceItemOut:
    return AdviceItemOut(
        id=str(i.id),
        title=i.title,
        category=i.category,
        body=i.body,
        relevance=i.relevance,
        disclaimer=i.disclaimer,
        is_read=i.is_read,
        created_at=i.created_at.isoformat() if i.created_at else None,
    )


async def _session_out(db: AsyncSession, session: AdviceSession) -> AdviceSessionOut:
    items = list(
        await db.scalars(
            select(AdviceItem)
            .where(AdviceItem.session_id == session.id)
            .order_by(AdviceItem.created_at.asc())
        )
    )
    return AdviceSessionOut(
        session_id=str(session.id),
        snapshot_id=str(session.snapshot_id) if session.snapshot_id else None,
        generated_at=session.generated_at.isoformat() if session.generated_at else None,
        advice_count=session.advice_count or len(items),
        items=[_item_out(i) for i in items],
    )


@router.post("/advice/generate", response_model=AdviceSessionOut)
@limiter.limit(ADVICE_LIMIT)
async def generate(
    request: Request,
    current: User = Depends(enforce_advice_quota),
    db: AsyncSession = Depends(get_db),
) -> AdviceSessionOut:
    graphs = _graphs(request)
    thread_id = f"advice-{uuid.uuid4()}"
    language = (current.settings or {}).get("lang", "ru")
    result = await runners.run_advisor(
        graphs, thread_id, {"user_id": str(current.id), "language": language}
    )

    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    session_id = result.get("advice_session_id")
    if not session_id:
        raise HTTPException(
            status_code=422,
            detail="No advice generated — add a confirmed portfolio first.",
        )

    session = await db.get(AdviceSession, uuid.UUID(session_id))
    if session is None:
        raise HTTPException(status_code=500, detail="Advice session not found after save")
    return await _session_out(db, session)


@router.get("/advice/latest", response_model=AdviceSessionOut | None)
async def latest(
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AdviceSessionOut | None:
    session = await db.scalar(
        select(AdviceSession)
        .where(AdviceSession.user_id == current.id)
        .order_by(AdviceSession.generated_at.desc())
        .limit(1)
    )
    if session is None:
        return None
    return await _session_out(db, session)


@router.post(
    "/advice/{item_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def mark_read(
    item_id: uuid.UUID,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    item = await db.get(AdviceItem, item_id)
    if item is None or item.user_id != current.id:
        raise HTTPException(status_code=404, detail="Advice item not found")
    item.is_read = True
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- WebSocket ----------------------------------------------------------------


async def _user_from_token(token: str | None) -> User | None:
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type="access")
        uid = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None
    async with SessionLocal() as s:
        return await s.get(User, uid)


@router.websocket("/ws/portfolio")
async def ws_portfolio(ws: WebSocket) -> None:
    """Authenticated push channel.

    Auth is the FIRST frame after connect: ``{"type":"auth","token":<access_jwt>}``.
    The token is sent in the body, not the URL — query strings leak into proxy
    and server access logs.
    """
    await ws.accept()
    token: str | None = None
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        parsed = json.loads(raw)
        token = parsed.get("token") if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001 — timeout / disconnect / non-text / bad JSON → no auth
        token = None
    user = await _user_from_token(token)
    if user is None:
        try:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        except Exception:  # noqa: BLE001 — client may already be gone
            pass
        return

    manager = get_ws_manager()
    user_id = str(user.id)
    await manager.register(user_id, ws)
    try:
        await ws.send_json({"type": "connected"})
        while True:
            # We don't expect client messages; this keeps the socket open and
            # detects disconnects.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(user_id, ws)
