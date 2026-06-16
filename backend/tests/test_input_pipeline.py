"""Срез 1 verification — input graph e2e with a deterministic (mocked) parser.

Reference phrase (plan §8):
    "10к евро налик в минске, 2 битка на кошельке, депозит 1000 лари в боге под 9%"

We mock the LLM parser so the test is deterministic and offline, then exercise
the REAL graph (parse -> validate -> enrich -> human_review interrupt ->
save_to_db) and the REAL persistence layer, asserting Decimal money lands in
the DB.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select

from agents.graph import compile_all
from agents.state import AssetItem, ParsedPortfolio
from core.db import SessionLocal
from models.asset import Asset
from models.snapshot import Snapshot
from models.user import User

REFERENCE = "10к евро налик в минске, 2 битка на кошельке, депозит 1000 лари в боге под 9%"


def _reference_portfolio() -> ParsedPortfolio:
    return ParsedPortfolio(
        assets=[
            AssetItem(
                asset_type="cash",
                amount=Decimal("10000"),
                currency="EUR",
                location="Minsk",
                country="BY",
                confidence=0.95,
            ),
            AssetItem(
                asset_type="crypto",
                amount=Decimal("2"),
                currency="BTC",
                symbol="BTC",
                quantity=Decimal("2"),
                confidence=0.97,
            ),
            AssetItem(
                asset_type="bank_deposit",
                amount=Decimal("1000"),
                currency="GEL",
                country="GE",
                location="Batumi",
                interest_rate=Decimal("9"),
                confidence=0.9,
            ),
        ],
        needs_review=False,
    )


@pytest.fixture
def graphs():
    return compile_all(MemorySaver())


@pytest.fixture(autouse=True)
def _mock_parser(monkeypatch):
    async def fake_parse_text(text: str, base_currency: str = "USD") -> ParsedPortfolio:
        return _reference_portfolio()

    # The parse node calls services.parser.parse_text by attribute at runtime.
    import services.parser as parser_service

    monkeypatch.setattr(parser_service, "parse_text", fake_parse_text)


async def _make_user() -> uuid.UUID:
    uid = uuid.uuid4()
    async with SessionLocal() as s:
        s.add(User(id=uid, email=f"{uid}@t.test", password_hash="x", base_currency="USD"))
        await s.commit()
    return uid


async def test_input_to_preview_to_save(db_ready, graphs):
    from agents import runners

    user_id = await _make_user()
    thread_id = str(uuid.uuid4())

    # 1) /input — runs to the human_review interrupt and returns a preview.
    preview = await runners.run_input(
        graphs,
        thread_id,
        {
            "user_id": str(user_id),
            "base_currency": "USD",
            "input_type": "text",
            "raw_text": REFERENCE,
        },
    )

    assert preview.get("snapshot_id") is None  # paused before saving
    assets = preview["assets"]
    assert len(assets) == 3
    assert {a.asset_type for a in assets} == {"cash", "crypto", "bank_deposit"}
    assert preview["validation"].is_valid

    # Graph is genuinely paused at human_review.
    state = await runners.get_pending(graphs, thread_id)
    assert state.next == ("human_review",)

    # 2) /confirm — resume and persist (no edits).
    result = await runners.resume_input(graphs, thread_id, edits=None)
    snapshot_id = result["snapshot_id"]
    assert snapshot_id

    # 3) Verify DB rows with Decimal precision.
    async with SessionLocal() as s:
        snap = await s.get(Snapshot, uuid.UUID(snapshot_id))
        assert snap is not None
        assert snap.is_confirmed is True
        assert snap.user_id == user_id

        rows = (
            await s.scalars(select(Asset).where(Asset.snapshot_id == snap.id))
        ).all()
        assert len(rows) == 3

        by_type = {r.asset_type: r for r in rows}
        assert by_type["cash"].amount == Decimal("10000")
        assert by_type["cash"].currency == "EUR"
        assert isinstance(by_type["cash"].amount, Decimal)
        assert by_type["crypto"].symbol == "BTC"
        assert by_type["crypto"].amount == Decimal("2")
        assert by_type["bank_deposit"].interest_rate == Decimal("9")
        assert by_type["bank_deposit"].currency == "GEL"


async def test_confirm_applies_human_edits(db_ready, graphs):
    from agents import runners

    user_id = await _make_user()
    thread_id = str(uuid.uuid4())

    await runners.run_input(
        graphs,
        thread_id,
        {"user_id": str(user_id), "base_currency": "USD", "raw_text": REFERENCE},
    )

    # User drops the crypto line and tweaks the cash amount before confirming.
    edited = [
        AssetItem(asset_type="cash", amount=Decimal("12000"), currency="EUR"),
        AssetItem(
            asset_type="bank_deposit",
            amount=Decimal("1000"),
            currency="GEL",
            interest_rate=Decimal("9"),
        ),
    ]
    result = await runners.resume_input(graphs, thread_id, edits={"assets": edited})

    async with SessionLocal() as s:
        rows = (
            await s.scalars(
                select(Asset).where(Asset.snapshot_id == uuid.UUID(result["snapshot_id"]))
            )
        ).all()
        assert len(rows) == 2
        assert {r.asset_type for r in rows} == {"cash", "bank_deposit"}
        cash = next(r for r in rows if r.asset_type == "cash")
        assert cash.amount == Decimal("12000")
