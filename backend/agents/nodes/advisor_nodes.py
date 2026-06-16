"""Advisor-graph nodes (agents #8–#10 + persistence/notify).

Pipeline:
    portfolio_loader -> [geo || news] -> advisor -> save_advice -> notify_user -> END

`geo` and `news` run in parallel (fan-out/fan-in) — both read the loaded
portfolio and write disjoint state channels.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from agents.state import AdvisorState, AssetItem
from core.db import SessionLocal
from core.ws import get_ws_manager
from models.advice import DISCLAIMER, AdviceItem, AdviceSession
from models.asset import Asset
from models.snapshot import Snapshot
from services import advisor as advisor_service
from services import geo as geo_service
from services import news as news_service


def _asset_to_item(a: Asset) -> AssetItem:
    return AssetItem(
        asset_type=a.asset_type,
        amount=a.amount if a.amount is not None else Decimal(0),
        currency=a.currency or "USD",
        country=a.country,
        location=a.location,
        symbol=a.symbol,
        ticker=a.ticker,
        quantity=a.quantity,
        interest_rate=a.interest_rate,
        is_owed_to_me=a.is_owed_to_me,
        usd_value=a.usd_value,
        usd_rate=a.usd_rate,
    )


async def portfolio_loader(state: AdvisorState) -> dict[str, Any]:
    """Load the user's latest confirmed snapshot + assets from the DB.

    If the caller already injected `assets` (e.g. tests / scenario graph), keep
    them.
    """
    if state.get("assets"):
        return {"trace": [f"portfolio_loader: {len(state['assets'])} preset asset(s)"]}

    user_id = state.get("user_id")
    if not user_id:
        return {"assets": [], "error": "portfolio_loader: missing user_id",
                "trace": ["portfolio_loader: no user_id"]}

    uid = uuid.UUID(str(user_id))
    async with SessionLocal() as s:
        snap = await s.scalar(
            select(Snapshot)
            .where(Snapshot.user_id == uid, Snapshot.is_confirmed.is_(True))
            .order_by(Snapshot.created_at.desc())
            .limit(1)
        )
        if snap is None:
            return {"assets": [], "trace": ["portfolio_loader: no snapshot"]}
        rows = list(await s.scalars(select(Asset).where(Asset.snapshot_id == snap.id)))

    assets = [_asset_to_item(a) for a in rows]
    return {
        "assets": assets,
        "snapshot_id": str(snap.id),
        "total_usd": snap.total_usd,
        "base_currency": snap.base_currency,
        "trace": [f"portfolio_loader: snapshot {snap.id}, {len(assets)} asset(s)"],
    }


# --- Agent #8: Geo & Opportunity (LLM_ADVISOR, runs in parallel) --------------


async def geo(state: AdvisorState) -> dict[str, Any]:
    assets = state.get("assets", []) or []
    analysis = await geo_service.analyze_geo(assets)
    return {
        "geo_analysis": analysis,
        "trace": [f"geo: {len(analysis.get('exposure', []))} region(s)"],
    }


# --- Agent #9: News Aggregator (LLM_NEWS_RANK, runs in parallel) --------------


async def news(state: AdvisorState) -> dict[str, Any]:
    assets = state.get("assets", []) or []
    items = await news_service.aggregate_news(assets)
    return {"news": items, "trace": [f"news: {len(items)} headline(s)"]}


# --- Agent #10: Advisor (LLM_ADVISOR) — generates insights --------------------


async def advisor(state: AdvisorState) -> dict[str, Any]:
    assets = state.get("assets", []) or []
    advice = await advisor_service.generate_advice(
        assets,
        total_usd=state.get("total_usd"),
        geo=state.get("geo_analysis"),
        news=state.get("news"),
        language=state.get("language", "ru"),
    )
    return {"advice": advice, "trace": [f"advisor: {len(advice)} insight(s)"]}


async def save_advice(state: AdvisorState) -> dict[str, Any]:
    """Persist AdviceSession + AdviceItem rows. Opens its own session."""
    user_id = state.get("user_id")
    advice = state.get("advice", []) or []
    if not user_id or not advice:
        return {"advice_session_id": None, "trace": ["save_advice: nothing to save"]}

    uid = uuid.UUID(str(user_id))
    snapshot_id = state.get("snapshot_id")

    async with SessionLocal() as s:
        session = AdviceSession(
            id=uuid.uuid4(),
            user_id=uid,
            snapshot_id=uuid.UUID(snapshot_id) if snapshot_id else None,
            advice_count=len(advice),
        )
        s.add(session)
        for item in advice:
            s.add(
                AdviceItem(
                    id=uuid.uuid4(),
                    session_id=session.id,
                    user_id=uid,
                    title=item["title"],
                    category=item.get("category"),
                    body=item["body"],
                    relevance=item.get("relevance"),
                    disclaimer=item.get("disclaimer", DISCLAIMER),
                )
            )
        await s.commit()
        sid = str(session.id)

    return {"advice_session_id": sid, "trace": [f"save_advice: session {sid}"]}


async def notify_user(state: AdvisorState) -> dict[str, Any]:
    """Push over WS `/ws/portfolio` that fresh advice is ready (best-effort)."""
    user_id = state.get("user_id")
    sid = state.get("advice_session_id")
    if user_id and sid:
        await get_ws_manager().broadcast(
            str(user_id),
            {"type": "advice_ready", "session_id": sid, "count": len(state.get("advice", []))},
        )
    return {"trace": ["notify_user: broadcast advice_ready"]}
