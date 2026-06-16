"""Request/response schemas for the Scenario ('what if') flow (Срез 4)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from agents.state import AssetItem


class ScenarioRequest(BaseModel):
    scenario_text: str = Field(min_length=1, description="Free-form 'what if' hypothesis")
    base_snapshot_id: Optional[str] = Field(
        default=None, description="Snapshot to base the scenario on (default: latest)"
    )
    base_currency: Optional[str] = None


class ScenarioResponse(BaseModel):
    result_total_usd: Optional[str] = None  # Decimal serialised as string
    comparison: dict[str, Any] = Field(default_factory=dict)
    assets: list[AssetItem] = Field(default_factory=list)
    advice: list[dict[str, Any]] = Field(default_factory=list)
    changes: list[dict[str, Any]] = Field(default_factory=list)
