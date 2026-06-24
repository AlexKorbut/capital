"""Nodes shared across graphs: supervisor routing + enrichment.

Every node is a thin async function: it takes the graph state and returns a
*partial* state update. Real logic lives in `services/` (wired in later slices);
here the nodes are runnable stubs that thread state and append to `trace`.
"""
from __future__ import annotations

from typing import Any

from agents.state import InputState, ScenarioState
from services import enrichment as enrichment_service

# --- Supervisor (agent #1): chooses which pipeline an input belongs to --------

# A purely deterministic mapping is enough at the skeleton stage; the LLM-backed
# supervisor (LLM_SUPERVISOR) refines this once intents get fuzzy.
_PIPELINES = {"input", "advisor", "scenario"}


async def supervisor(state: dict[str, Any]) -> dict[str, Any]:
    """Annotate which pipeline should handle this request.

    TODO(slice-3+): replace heuristic with `core.llm.get_model(SUPERVISOR)`
    classification when requests arrive via a single unified entrypoint.
    """
    pipeline = state.get("pipeline", "input")
    if pipeline not in _PIPELINES:
        pipeline = "input"
    return {"pipeline": pipeline, "trace": [f"supervisor -> {pipeline}"]}


# --- Enrichment (agent #7): attach USD value to every asset -------------------


async def enrich(state: InputState | ScenarioState) -> dict[str, Any]:
    """Convert each asset to USD using cached FX / price data (Decimal math)."""
    assets = state.get("assets", []) or []
    base_currency = state.get("base_currency", "USD") or "USD"
    assets, total_usd = await enrichment_service.enrich_assets(assets, base_currency)
    priced = sum(1 for a in assets if a.usd_value is not None)
    return {
        "assets": assets,
        "total_usd": total_usd,
        "trace": [f"enrich: {priced}/{len(assets)} priced, total_usd={total_usd}"],
    }
