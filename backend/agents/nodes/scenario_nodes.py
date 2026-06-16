"""Scenario-graph nodes (agent #11 + comparison).

Pipeline:
    scenario_parser -> portfolio_simulator -> enrich -> advisor -> comparison -> END

`scenario_parser` loads the user's current (baseline) portfolio, enriches it for
a clean comparison baseline, and parses the "what if" text into structured
changes. `portfolio_simulator` applies those changes (pure Decimal math); the
shared `enrich` node then prices the hypothetical portfolio, `advisor` analyses
it, and `comparison` diffs hypothetical vs. baseline.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from agents.nodes.advisor_nodes import _asset_to_item
from agents.state import ScenarioState
from core.db import SessionLocal
from models.asset import Asset
from models.snapshot import Snapshot
from services import enrichment as enrichment_service
from services import scenario as scenario_service


async def _load_base_assets(state: ScenarioState) -> list:
    """Use preset base_assets/assets if present, else load latest confirmed snapshot."""
    preset = state.get("base_assets") or state.get("assets")
    if preset:
        return list(preset)

    user_id = state.get("user_id")
    if not user_id:
        return []

    uid = uuid.UUID(str(user_id))
    async with SessionLocal() as s:
        snap = await s.scalar(
            select(Snapshot)
            .where(Snapshot.user_id == uid, Snapshot.is_confirmed.is_(True))
            .order_by(Snapshot.created_at.desc())
            .limit(1)
        )
        if snap is None:
            return []
        rows = list(await s.scalars(select(Asset).where(Asset.snapshot_id == snap.id)))
    return [_asset_to_item(a) for a in rows]


# --- Agent #11: Scenario Simulator (LLM_SCENARIO) -----------------------------


async def scenario_parser(state: ScenarioState) -> dict[str, Any]:
    """Load + enrich the baseline portfolio, then parse the 'what if' into changes."""
    base_assets = await _load_base_assets(state)
    base_assets, base_total = await enrichment_service.enrich_assets(base_assets)

    changes_obj = await scenario_service.parse_changes(
        state.get("scenario_text", "") or "", base_assets
    )
    return {
        "base_assets": base_assets,
        "base_total_usd": base_total,
        "changes": [c.model_dump() for c in changes_obj.changes],
        "trace": [f"scenario_parser: {len(changes_obj.changes)} change(s)"],
    }


async def portfolio_simulator(state: ScenarioState) -> dict[str, Any]:
    """Apply the parsed changes to the baseline -> the hypothetical asset set."""
    base_assets = state.get("base_assets", []) or []
    changes = state.get("changes", []) or []
    new_assets = scenario_service.apply_changes(base_assets, changes)
    return {
        "assets": new_assets,
        "trace": [f"portfolio_simulator: {len(new_assets)} hypothetical asset(s)"],
    }


async def comparison(state: ScenarioState) -> dict[str, Any]:
    """Diff hypothetical vs. baseline totals/breakdown for the UI."""
    comp = scenario_service.compare(
        state.get("base_assets", []) or [],
        state.get("base_total_usd"),
        state.get("assets", []) or [],
        state.get("total_usd"),
    )
    return {
        "comparison": comp,
        "result_total_usd": state.get("total_usd"),
        "trace": [f"comparison: delta={comp.get('delta_usd')}"],
    }
