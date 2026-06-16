"""Request/response schemas for the portfolio input + confirm flow (Срез 1)."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from agents.state import AssetItem, ValidationResult

# AssetItem (from agents.state) is reused directly as the wire shape for assets
# — it already carries Decimal amounts and all type-specific fields.


class InputRequest(BaseModel):
    text: str = Field(min_length=1, description="Free-form assets description")
    input_type: str = Field(default="text")
    base_currency: Optional[str] = Field(
        default=None, description="Overrides the user's default base currency"
    )


class PreviewResponse(BaseModel):
    """Returned after parsing — the graph is paused at human_review."""

    thread_id: str
    assets: list[AssetItem]
    validation: Optional[ValidationResult] = None
    needs_review: bool = False
    total_usd: Optional[str] = None  # Decimal serialised as string (or None pre-enrich)


class ConfirmRequest(BaseModel):
    thread_id: str
    # Optional human-edited asset list; if omitted, the parsed assets are saved.
    assets: Optional[list[AssetItem]] = None


class ConfirmResponse(BaseModel):
    snapshot_id: str
    assets: list[AssetItem]
    total_usd: Optional[str] = None


# --- Read side (dashboard) ----------------------------------------------------


class BreakdownEntry(BaseModel):
    key: str
    usd_value: Optional[str] = None


class Breakdown(BaseModel):
    by_type: list[BreakdownEntry] = Field(default_factory=list)
    by_currency: list[BreakdownEntry] = Field(default_factory=list)
    by_country: list[BreakdownEntry] = Field(default_factory=list)


class AssetRow(BaseModel):
    id: str
    asset_type: str
    amount: Optional[str] = None
    currency: Optional[str] = None
    country: Optional[str] = None
    location: Optional[str] = None
    usd_value: Optional[str] = None
    usd_rate: Optional[str] = None
    symbol: Optional[str] = None
    ticker: Optional[str] = None
    quantity: Optional[str] = None
    interest_rate: Optional[str] = None
    appreciation_rate: Optional[str] = None
    estimated_usd: Optional[str] = None
    is_owed_to_me: Optional[bool] = None


class CurrentPortfolio(BaseModel):
    snapshot_id: str
    created_at: Optional[str] = None
    total_usd: Optional[str] = None
    estimated_total_usd: Optional[str] = None
    base_currency: Optional[str] = None
    usd_per_base: Optional[str] = None
    assets: list[AssetRow] = Field(default_factory=list)
    breakdown: Breakdown


class SnapshotSummary(BaseModel):
    snapshot_id: str
    created_at: Optional[str] = None
    total_usd: Optional[str] = None
    base_currency: Optional[str] = None


class HistoryPoint(BaseModel):
    date: Optional[str] = None
    total_usd: Optional[str] = None


# --- Returns / net-worth growth (Feature #1) ---------------------------------


class ReturnWindow(BaseModel):
    key: str
    label: str
    baseline_usd: Optional[str] = None
    baseline_date: Optional[str] = None
    change_usd: Optional[str] = None
    change_pct: Optional[str] = None
    partial: bool = False


class ReturnsResponse(BaseModel):
    current_usd: Optional[str] = None
    as_of: Optional[str] = None
    snapshots_count: int = 0
    span_days: int = 0
    first_date: Optional[str] = None
    cagr_pct: Optional[str] = None
    windows: list[ReturnWindow] = Field(default_factory=list)


# --- Target allocation & drift (Feature #7) ----------------------------------


class AllocationRow(BaseModel):
    asset_type: str
    current_pct: str
    target_pct: Optional[str] = None
    drift_pct: Optional[str] = None


class AllocationResponse(BaseModel):
    has_target: bool = False
    rows: list[AllocationRow] = Field(default_factory=list)


class AllocationUpdate(BaseModel):
    targets: dict[str, float] = Field(default_factory=dict)


# --- Manual asset edit (add / update / delete one position) ------------------


class AssetUpsert(BaseModel):
    asset_type: str
    amount: Decimal = Field(gt=0)
    currency: str = "USD"
    country: Optional[str] = None
    location: Optional[str] = None
    note: Optional[str] = None
    ticker: Optional[str] = None
    symbol: Optional[str] = None
    quantity: Optional[Decimal] = None
    interest_rate: Optional[Decimal] = None
    appreciation_rate: Optional[Decimal] = None
    counterparty: Optional[str] = None
    is_owed_to_me: Optional[bool] = None
