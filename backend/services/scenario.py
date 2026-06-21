"""Scenario Simulator service (Agent #11) — "what if" analysis.

Pipeline (see ``agents/nodes/scenario_nodes.py``):
    parse_changes  -> apply_changes -> (enrich) -> compare

`parse_changes` turns a free-form hypothetical ("что если продам биткоин и
куплю на это евро") into a structured list of portfolio mutations via the
provider-agnostic ``LLM_SCENARIO`` model. `apply_changes` is pure Decimal math
on ``AssetItem``s; `compare` diffs the hypothetical against the baseline.

No money ever touches ``float``.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from agents.state import AssetItem, AssetType
from core.config import settings
from core.llm import ModelRole, structured

logger = logging.getLogger("kapital.scenario")

_USD_QUANT = Decimal("0.01")


# --- Structured LLM output ----------------------------------------------------


class ScenarioChange(BaseModel):
    """One mutation to apply to the baseline portfolio."""

    action: Literal["add", "remove", "update"] = Field(
        description="add a new asset, remove an existing one, or update its amount"
    )
    asset_type: AssetType = Field(default="other")
    amount: Optional[Decimal] = Field(
        default=None, description="New/added quantity in the asset's own currency/unit"
    )
    currency: str = Field(default="USD", description="ISO-4217 code or crypto/ticker symbol")
    symbol: Optional[str] = Field(default=None, description="Crypto/ticker symbol if relevant")
    quantity: Optional[Decimal] = None
    target: Optional[str] = Field(
        default=None,
        description="Which existing asset this refers to (symbol/currency/type) "
        "for remove/update",
    )
    note: Optional[str] = None


class ScenarioChanges(BaseModel):
    changes: list[ScenarioChange] = Field(default_factory=list)
    summary: Optional[str] = Field(default=None, description="One-line plain summary")


_SYSTEM = """\
Ты — движок «что если» для трекера капитала. На вход — текущий портфель и
гипотеза пользователя на естественном языке. Преобразуй гипотезу в список
СТРУКТУРНЫХ изменений (changes), не давая никаких советов.

Действия:
- add: добавить новый актив (asset_type, amount, currency/symbol).
- remove: убрать существующий актив (target = его символ/валюта/тип).
- update: изменить количество существующего актива (target + новый amount).

Правила:
- "продам X" / "избавлюсь от X" -> remove с target=X.
- "куплю Y на сумму" / "добавлю Y" -> add.
- "переложу X в Y" -> remove X + add Y.
- Расширяй сокращения: "10к" = 10000.
- Только структура, без прозы и без рекомендаций.
"""


def _portfolio_brief(assets: list[AssetItem]) -> str:
    if not assets:
        return "Портфель пуст."
    lines = []
    for a in assets:
        usd = f"${a.usd_value}" if a.usd_value is not None else "оценка н/д"
        lines.append(
            f"- {a.asset_type}: {a.amount} {a.symbol or a.currency or ''} ({usd})"
        )
    return "Текущий портфель:\n" + "\n".join(lines)


async def parse_changes(
    scenario_text: str, assets: list[AssetItem]
) -> ScenarioChanges:
    """Parse a 'what if' instruction into structured changes (no LLM if empty)."""
    text = (scenario_text or "").strip()
    if not text:
        return ScenarioChanges(changes=[], summary="Пустой сценарий.")

    if settings.is_demo:
        from core.demo import demo_scenario_changes
        return ScenarioChanges.model_validate(demo_scenario_changes(text, assets))

    model = structured(ModelRole.SCENARIO, ScenarioChanges)
    messages = [
        ("system", _SYSTEM),
        ("human", f"{_portfolio_brief(assets)}\n\nГипотеза:\n{text}"),
    ]
    return await model.ainvoke(messages)


# --- Pure Decimal mutation ----------------------------------------------------


def _matches(asset: AssetItem, target: str | None, asset_type: str | None) -> bool:
    """Loose match of an existing asset by symbol/currency/type."""
    if target:
        t = target.strip().lower()
        # Symbol/currency/ticker/type must match EXACTLY (case-insensitive) so a
        # target like "USD" never matches a "USDT" asset and short targets don't
        # over-match the wrong asset.
        for field in (asset.symbol, asset.currency, asset.ticker, asset.asset_type, asset.location):
            if field and t == str(field).lower():
                return True
        # Looser contains-match only for free-text location.
        if asset.location and (t in str(asset.location).lower() or str(asset.location).lower() in t):
            return True
        return False
    if asset_type and asset_type != "other":
        return asset.asset_type == asset_type
    return False


def _as_change(c: Any) -> ScenarioChange:
    if isinstance(c, ScenarioChange):
        return c
    return ScenarioChange.model_validate(c)


def apply_changes(
    base_assets: list[AssetItem], changes: list[Any]
) -> list[AssetItem]:
    """Return a NEW asset list with the changes applied (baseline untouched)."""
    # Deep-ish copy so we never mutate the caller's baseline assets.
    result: list[AssetItem] = [a.model_copy(deep=True) for a in base_assets]

    for raw in changes or []:
        change = _as_change(raw)

        if change.action == "add":
            result.append(
                AssetItem(
                    asset_type=change.asset_type,
                    amount=change.amount if change.amount is not None else Decimal(0),
                    currency=(change.symbol or change.currency or "USD"),
                    symbol=change.symbol,
                    quantity=change.quantity,
                    note=change.note,
                )
            )
            continue

        # remove / update need to locate the target asset
        idx = next(
            (
                i
                for i, a in enumerate(result)
                if _matches(a, change.target, change.asset_type)
            ),
            None,
        )
        if idx is None:
            logger.info("scenario: no asset matched target=%r — skipping", change.target)
            continue

        if change.action == "remove":
            result.pop(idx)
        elif change.action == "update" and change.amount is not None:
            result[idx].amount = change.amount
            if result[idx].asset_type == "crypto":
                result[idx].quantity = change.amount

    return result


# --- Comparison ---------------------------------------------------------------


def _q(value: Decimal | None) -> Decimal | None:
    return value.quantize(_USD_QUANT) if value is not None else None


def compare(
    base_assets: list[AssetItem],
    base_total: Decimal | None,
    new_assets: list[AssetItem],
    new_total: Decimal | None,
) -> dict[str, Any]:
    """Diff hypothetical vs. baseline for the UI (all Decimal, serialised str)."""
    delta: Decimal | None = None
    pct: Decimal | None = None
    if base_total is not None and new_total is not None:
        delta = _q(new_total - base_total)
        if base_total != 0:
            pct = ((new_total - base_total) / base_total * Decimal(100)).quantize(
                Decimal("0.1")
            )

    return {
        "base_total_usd": str(base_total) if base_total is not None else None,
        "new_total_usd": str(new_total) if new_total is not None else None,
        "delta_usd": str(delta) if delta is not None else None,
        "delta_pct": str(pct) if pct is not None else None,
        "base_asset_count": len(base_assets),
        "new_asset_count": len(new_assets),
    }
