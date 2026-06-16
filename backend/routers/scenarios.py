"""Scenarios router — 'what if' simulation (Срез 4).

    POST /scenarios/simulate   run the Scenario graph on the user's portfolio.

The scenario graph does NOT persist anything — it returns a hypothetical
comparison the user can inspect without touching their real snapshots.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from agents import runners
from agents.state import AssetItem
from core.tiers import require_scenarios
from models.user import User
from schemas.scenario import ScenarioRequest, ScenarioResponse

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


def _graphs(request: Request) -> runners.GraphRegistry:
    graphs = getattr(request.app.state, "graphs", None)
    if graphs is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Graph runtime not ready",
        )
    return graphs


def _as_asset_items(raw: Any) -> list[AssetItem]:
    return [a if isinstance(a, AssetItem) else AssetItem.model_validate(a) for a in raw or []]


@router.post("/simulate", response_model=ScenarioResponse)
async def simulate_scenario(
    body: ScenarioRequest,
    request: Request,
    current: User = Depends(require_scenarios),
) -> ScenarioResponse:
    graphs = _graphs(request)
    thread_id = f"scenario-{uuid.uuid4()}"
    base_currency = (body.base_currency or current.base_currency or "USD").upper()

    state = {
        "user_id": str(current.id),
        "base_currency": base_currency,
        "base_snapshot_id": body.base_snapshot_id,
        "scenario_text": body.scenario_text,
    }
    result = await runners.run_scenario(graphs, thread_id, state)

    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    total = result.get("result_total_usd")
    return ScenarioResponse(
        result_total_usd=str(total) if total is not None else None,
        comparison=result.get("comparison") or {},
        assets=_as_asset_items(result.get("assets")),
        advice=result.get("advice") or [],
        changes=result.get("changes") or [],
    )
