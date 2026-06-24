"""Geo & Opportunity service (Agent #8).

Two layers:
  1. Deterministic exposure — group ``usd_value`` by country (pure Decimal),
     compute share-of-portfolio. Always available, no LLM/network.
  2. Qualitative observations — an LLM (ADVISOR role) adds brief, NON-prescriptive
     notes about diversification/concentration per region. Degrades to an empty
     list if the model is unavailable.

Like the Advisor, the language is analytical, never 'buy/sell/recommend'.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from agents.state import AssetItem
from core.config import settings
from core.llm import ModelRole, structured

logger = logging.getLogger("kapital.geo")


class GeoObservation(BaseModel):
    region: str = Field(description="Country/region the note is about, or 'global'")
    note: str = Field(description="Brief analytical observation — never advice")


class GeoObservations(BaseModel):
    observations: list[GeoObservation] = Field(default_factory=list)


_SYSTEM = """\
You analyse the geographic distribution of a private portfolio. Provide brief,
neutral, ANALYTICAL observations about concentration and diversification by
country/region.

Hard rules:
- NEVER tell the user to buy, sell, move, or reallocate anything.
- NEVER use the words рекомендую/советую/купите/продайте/buy/sell.
- Describe what IS (e.g. "84% капитала сосредоточено в одной стране"), not what
  to do. Keep each note to one sentence. Reply in the user's language (Russian).
"""


def compute_exposure(assets: list[AssetItem]) -> list[dict[str, Any]]:
    """Country -> {usd_value, pct} sorted desc. Ignores unpriced assets."""
    by_country: dict[str, Decimal] = {}        # signed value, for display
    by_country_exposure: dict[str, Decimal] = {}  # abs value, for the share math
    total = Decimal(0)
    for a in assets:
        if a.usd_value is None:
            continue
        # Net-worth contribution can be negative (debts); use abs for *exposure*
        # share but keep signed value for display.
        country = a.country or "—"
        by_country[country] = by_country.get(country, Decimal(0)) + a.usd_value
        by_country_exposure[country] = (
            by_country_exposure.get(country, Decimal(0)) + abs(a.usd_value)
        )
        total += abs(a.usd_value)

    rows: list[dict[str, Any]] = []
    for country, value in by_country.items():
        exposure = by_country_exposure.get(country, Decimal(0))
        pct = (exposure / total * Decimal(100)) if total and total != 0 else None
        rows.append(
            {
                "country": country,
                "usd_value": str(value),
                "pct": str(pct.quantize(Decimal("0.1"))) if pct is not None else None,
            }
        )
    rows.sort(key=lambda r: Decimal(r["usd_value"]), reverse=True)
    return rows


async def analyze_geo(assets: list[AssetItem]) -> dict[str, Any]:
    exposure = compute_exposure(assets)
    observations: list[dict[str, str]] = []

    if exposure and settings.is_demo:
        from core.demo import demo_geo_observations
        return {"exposure": exposure, "observations": demo_geo_observations(exposure)}

    if exposure:
        try:
            model = structured(ModelRole.ADVISOR, GeoObservations)
            summary = "; ".join(
                f"{r['country']}: {r['pct']}%" for r in exposure if r["pct"]
            )
            result: GeoObservations = await model.ainvoke(
                [
                    ("system", _SYSTEM),
                    ("human", f"Распределение капитала по странам: {summary}."),
                ]
            )
            observations = [o.model_dump() for o in result.observations]
        except Exception as e:  # noqa: BLE001 — geo is best-effort
            logger.warning("geo LLM observations skipped: %s", e)

    return {"exposure": exposure, "observations": observations}
