"""Срез 3 verification — advisor guardrails + graph e2e (mocked LLM).

Critical legal property: advisor output must NEVER read as investment advice and
must ALWAYS carry the disclaimer. We assert both on a deterministic (mocked)
model that deliberately tries to emit a banned phrase.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest

from models.advice import DISCLAIMER

# Phrases that must never appear in shown advice.
_BANNED = re.compile(
    r"купи|продай|рекоменд|совет|инвестируй|\bbuy\b|\bsell\b|recommend",
    re.IGNORECASE,
)


class _FakeRunnable:
    def __init__(self, result):
        self._result = result

    async def ainvoke(self, _messages):
        return self._result


@pytest.fixture
def mock_llm(monkeypatch):
    """Make advisor + geo structured() return canned outputs (no network)."""
    import services.advisor as advisor_service
    import services.geo as geo_service
    from services.advisor import AdvisorOutput, Insight
    from services.geo import GeoObservations

    advisor_output = AdvisorOutput(
        insights=[
            Insight(
                title="Высокая валютная концентрация",
                category="currency",
                body="68% капитала в EUR. Это повышает чувствительность к курсу евро.",
                relevance="Наличные EUR",
            ),
            # This one deliberately contains a banned word to exercise the filter.
            Insight(
                title="Купите больше биткоина",
                category="risk",
                body="Советую купить ещё BTC, пока цена низкая.",
                relevance="Крипто",
            ),
        ]
    )

    monkeypatch.setattr(
        advisor_service, "structured", lambda *a, **k: _FakeRunnable(advisor_output)
    )
    monkeypatch.setattr(
        geo_service, "structured", lambda *a, **k: _FakeRunnable(GeoObservations())
    )


async def test_generate_advice_sanitizes_and_disclaims(mock_llm):
    from agents.state import AssetItem
    from services.advisor import generate_advice

    assets = [
        AssetItem(asset_type="cash", amount=Decimal("10000"), currency="EUR",
                  usd_value=Decimal("10800")),
    ]
    advice = await generate_advice(assets, total_usd=Decimal("10800"))

    assert len(advice) == 2
    for item in advice:
        assert item["disclaimer"] == DISCLAIMER
        assert not _BANNED.search(item["title"]), item["title"]
        assert not _BANNED.search(item["body"]), item["body"]

    # The clean insight survives; the banned one was neutralised (title changed).
    titles = [i["title"] for i in advice]
    assert "Высокая валютная концентрация" in titles
    assert "Купите больше биткоина" not in titles


async def test_advisor_graph_e2e_persists_clean_advice(db_ready, mock_llm):
    from langgraph.checkpoint.memory import MemorySaver
    from sqlalchemy import select

    from agents import runners
    from agents.graph import compile_all
    from core.db import SessionLocal
    from models.advice import AdviceItem, AdviceSession
    from models.asset import Asset
    from models.snapshot import Snapshot
    from models.user import User

    # Seed a confirmed portfolio.
    uid = uuid.uuid4()
    async with SessionLocal() as s:
        s.add(User(id=uid, email=f"{uid}@t.test", password_hash="x", base_currency="USD"))
        snap = Snapshot(id=uuid.uuid4(), user_id=uid, total_usd=Decimal("10800"),
                        base_currency="USD", is_confirmed=True)
        s.add(snap)
        s.add(Asset(id=uuid.uuid4(), snapshot_id=snap.id, user_id=uid, asset_type="cash",
                    currency="EUR", country="BY", amount=Decimal("10000"),
                    usd_value=Decimal("10800")))
        await s.commit()

    graphs = compile_all(MemorySaver())
    result = await runners.run_advisor(graphs, f"advice-{uuid.uuid4()}", {"user_id": str(uid)})

    session_id = result["advice_session_id"]
    assert session_id

    async with SessionLocal() as s:
        session = await s.get(AdviceSession, uuid.UUID(session_id))
        assert session is not None
        assert session.user_id == uid
        items = list(
            await s.scalars(select(AdviceItem).where(AdviceItem.session_id == session.id))
        )
        assert len(items) == 2
        for it in items:
            assert it.disclaimer == DISCLAIMER
            assert not _BANNED.search(it.title)
            assert not _BANNED.search(it.body)
