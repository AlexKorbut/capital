"""Graph state + the structured schemas LLM nodes produce.

LangGraph state is a `TypedDict` (channels merged per-node). The *contents* an
LLM must emit are Pydantic models so we can use provider-agnostic
`with_structured_output` (see `core.llm.structured`) instead of brittle JSON
parsing.

Money is ``Decimal`` everywhere — never float.
"""
from __future__ import annotations

import operator
from decimal import Decimal
from typing import Annotated, Any, Literal, Optional, TypedDict

from pydantic import BaseModel, Field

# --- Domain enums (kept as plain literals for portability) ---------------------

AssetType = Literal[
    "cash",
    "bank_deposit",
    "crypto",
    "stock",
    "real_estate",
    "vehicle",
    "debt",
    "other",
]

InputType = Literal["text", "voice", "file", "image"]


# --- Structured LLM outputs ----------------------------------------------------


class AssetItem(BaseModel):
    """One parsed asset. The Parser agent emits a list of these."""

    asset_type: AssetType = Field(description="Category of the asset")
    amount: Decimal = Field(description="Quantity in the asset's own currency/unit")
    currency: str = Field(default="USD", description="ISO-4217 code, or crypto/ticker symbol")
    country: Optional[str] = Field(default=None, description="ISO country where the asset is held")
    location: Optional[str] = Field(default=None, description="Free-text place / bank / wallet label")
    note: Optional[str] = Field(default=None, description="Any extra detail the user mentioned")

    # type-specific (all optional; only set when relevant)
    ticker: Optional[str] = None
    symbol: Optional[str] = None
    quantity: Optional[Decimal] = None
    interest_rate: Optional[Decimal] = None
    wallet_address: Optional[str] = None
    counterparty: Optional[str] = None
    is_owed_to_me: Optional[bool] = None
    # Real estate / vehicles: annual %% the value is expected to change. Positive
    # for appreciating property, negative for depreciating cars. Used to estimate
    # current value between snapshots (see services.enrichment / portfolio_read).
    appreciation_rate: Optional[Decimal] = None

    # enrichment (filled by the Enrichment node, not the LLM)
    usd_value: Optional[Decimal] = None
    usd_rate: Optional[Decimal] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ParsedPortfolio(BaseModel):
    """Top-level structured output of the Parser agent."""

    assets: list[AssetItem] = Field(default_factory=list)
    needs_review: bool = Field(
        default=False,
        description="True if any item is ambiguous and should go to human review",
    )
    parser_notes: Optional[str] = None


class ValidationResult(BaseModel):
    is_valid: bool = True
    needs_review: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --- Graph states (TypedDicts) -------------------------------------------------


class InputState(TypedDict, total=False):
    """State threaded through the Input graph."""

    # identity / config
    user_id: str
    base_currency: str
    # raw input
    input_type: InputType
    raw_text: str
    raw_bytes: Optional[bytes]
    mime_type: Optional[str]
    filename: Optional[str]
    # pipeline products
    transcript: Optional[str]
    assets: list[AssetItem]
    validation: Optional[ValidationResult]
    needs_review: bool
    snapshot_id: Optional[str]
    total_usd: Optional[Decimal]
    # accumulating log for observability / debugging
    trace: Annotated[list[str], operator.add]
    error: Optional[str]


class AdvisorState(TypedDict, total=False):
    user_id: str
    snapshot_id: Optional[str]
    base_currency: str
    language: str
    assets: list[AssetItem]
    total_usd: Optional[Decimal]
    geo_analysis: Optional[dict[str, Any]]
    news: list[dict[str, Any]]
    advice: list[dict[str, Any]]
    advice_session_id: Optional[str]
    trace: Annotated[list[str], operator.add]
    error: Optional[str]


class ScenarioState(TypedDict, total=False):
    user_id: str
    base_snapshot_id: Optional[str]
    base_currency: str
    scenario_text: str
    changes: list[dict[str, Any]]
    # baseline (current) portfolio, enriched, for the comparison
    base_assets: list[AssetItem]
    base_total_usd: Optional[Decimal]
    # hypothetical portfolio after applying `changes`
    assets: list[AssetItem]
    total_usd: Optional[Decimal]
    result_total_usd: Optional[Decimal]
    comparison: Optional[dict[str, Any]]
    advice: list[dict[str, Any]]
    trace: Annotated[list[str], operator.add]
    error: Optional[str]
