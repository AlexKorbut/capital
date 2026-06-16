"""Срез 4 verification — scenario simulator (Decimal) + graph e2e (mocked LLM).

The scenario engine must do exact Decimal math on a *copy* of the baseline
(never mutate the real portfolio) and produce a clean hypothetical comparison.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from agents.state import AssetItem


def _btc(qty: str) -> AssetItem:
    return AssetItem(
        asset_type="crypto", amount=Decimal(qty), currency="BTC", symbol="BTC",
        quantity=Decimal(qty),
    )


def _eur(amount: str) -> AssetItem:
    return AssetItem(asset_type="cash", amount=Decimal(amount), currency="EUR")


# --- Pure Decimal mutation (no LLM, no DB) ------------------------------------


def test_apply_changes_add_remove_update_is_pure():
    from services.scenario import apply_changes

    base = [_btc("2"), _eur("1000")]
    changes = [
        {"action": "remove", "target": "BTC"},
        {"action": "add", "asset_type": "cash", "amount": "5000", "currency": "EUR"},
        {"action": "update", "target": "EUR", "amount": "1500"},
    ]
    result = apply_changes(base, changes)

    # Baseline untouched (pure function).
    assert base[0].amount == Decimal("2")
    assert base[1].amount == Decimal("1000")

    # BTC removed; original EUR updated to 1500; new 5000 EUR added.
    kinds = [(a.asset_type, a.currency, a.amount) for a in result]
    assert ("crypto", "BTC", Decimal("2")) not in kinds
    assert ("cash", "EUR", Decimal("1500")) in kinds
    assert ("cash", "EUR", Decimal("5000")) in kinds
    assert len(result) == 2


def test_compare_computes_decimal_delta():
    from services.scenario import compare

    comp = compare([_btc("1")], Decimal("60000"), [_eur("6000")], Decimal("6480"))
    assert comp["base_total_usd"] == "60000"
    assert comp["new_total_usd"] == "6480"
    assert comp["delta_usd"] == "-53520.00"
    assert comp["delta_pct"] == "-89.2"


# --- Graph e2e (mocked parse_changes + advisor; patched market rates) ---------


@pytest.fixture
def _scenario_mocks(monkeypatch):
    import services.advisor as advisor_service
    import services.market as market
    import services.scenario as scenario_service
    from services.scenario import ScenarioChange, ScenarioChanges

    async def _eur_rate(ccy: str):
        return Decimal("1.08") if ccy.upper() == "EUR" else (
            Decimal("1") if ccy.upper() == "USD" else None
        )

    async def _btc_price(symbol: str):
        return Decimal("60000") if symbol.upper() == "BTC" else None

    async def _fake_parse(text, assets):
        return ScenarioChanges(
            changes=[
                ScenarioChange(action="remove", target="BTC"),
                ScenarioChange(
                    action="add", asset_type="cash", amount=Decimal("5000"),
                    currency="EUR",
                ),
            ]
        )

    async def _no_advice(*a, **k):
        return []

    monkeypatch.setattr(market, "usd_rate_for_currency", _eur_rate)
    monkeypatch.setattr(market, "usd_price_for_crypto", _btc_price)
    monkeypatch.setattr(scenario_service, "parse_changes", _fake_parse)
    monkeypatch.setattr(advisor_service, "generate_advice", _no_advice)


async def test_scenario_graph_e2e_compares_totals(_scenario_mocks):
    from langgraph.checkpoint.memory import MemorySaver

    from agents import runners
    from agents.graph import compile_all

    graphs = compile_all(MemorySaver())
    state = {
        "user_id": str(uuid.uuid4()),
        "base_currency": "USD",
        "scenario_text": "продам биткоин и добавлю 5000 евро",
        # preset baseline so the graph doesn't need a DB snapshot
        "base_assets": [_btc("1"), _eur("1000")],
    }
    result = await runners.run_scenario(graphs, f"sc-{uuid.uuid4()}", state)

    assert result.get("error") is None
    comp = result["comparison"]
    # base = 1 BTC ($60000) + 1000 EUR ($1080) = $61080
    assert comp["base_total_usd"] == "61080.00"
    # new  = 1000 EUR + 5000 EUR = 6000 EUR = $6480
    assert comp["new_total_usd"] == "6480.00"
    assert comp["delta_usd"] == "-54600.00"
    assert result["result_total_usd"] == Decimal("6480.00")
