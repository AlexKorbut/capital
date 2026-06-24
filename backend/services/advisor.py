"""Advisor service (Agent #10) — analytical insights, NOT investment advice.

This is the legally sensitive core. The output must read as neutral analysis of
the user's own data — never as a recommendation to buy/sell/hold a security.

Guardrails (defence in depth):
  1. A strict system prompt forbidding prescriptive language.
  2. A post-generation filter that flags banned phrases; flagged items are
     softened (rewritten label) rather than shown verbatim.
  3. A DISCLAIMER attached to every item at persistence time.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from agents.state import AssetItem
from core.config import settings
from core.llm import ModelRole, structured
from models.advice import DISCLAIMER

logger = logging.getLogger("kapital.advisor")

# Words/phrases that would make output sound like investment advice.
_BANNED = [
    r"\bкупи\w*", r"\bпродай\w*", r"\bпродав\w*", r"\bрекоменд\w*",
    r"\bсовету\w*", r"\bпосовету\w*", r"\bвлож\w*\s+в\b", r"\bинвестируй\w*",
    r"\bbuy\b", r"\bsell\b", r"\brecommend\w*", r"\bshould\s+(buy|sell|invest)\b",
]
_BANNED_RE = re.compile("|".join(_BANNED), re.IGNORECASE)


class Insight(BaseModel):
    title: str = Field(description="Short headline of the observation")
    category: str = Field(
        default="general",
        description="One of: diversification | concentration | currency | "
        "liquidity | risk | tax | general",
    )
    body: str = Field(description="2-4 sentences of neutral analysis of the user's data")
    relevance: str | None = Field(
        default=None, description="Which part of the portfolio this refers to"
    )


class AdvisorOutput(BaseModel):
    insights: list[Insight] = Field(default_factory=list)


_SYSTEM = """\
Ты — аналитик личного капитала. Ты описываешь СОБСТВЕННЫЕ данные пользователя:
структуру, концентрацию, валютные и страновые риски, ликвидность. Пиши по-русски,
нейтрально и по делу.

СТРОГО ЗАПРЕЩЕНО:
- Давать инвестиционные рекомендации. Никогда не пиши «купи», «продай», «вложи»,
  «рекомендую», «советую», buy, sell, recommend.
- Предлагать конкретные сделки, тикеры «на покупку», тайминг рынка, прогнозы цен.
- Обещать доходность.

МОЖНО:
- Констатировать факты о портфеле («68% в одной валюте», «нет ликвидного резерва»).
- Объяснять, какие риски ВЫТЕКАЮТ из текущей структуры, нейтральным языком.
- Задавать пользователю вопросы для размышления («стоит ли держать столько в …?»
  — без указания, что делать).

Каждый инсайт: title (коротко), category, body (2–4 предложения), relevance.
Сгенерируй 3–6 инсайтов. Только анализ, не рекомендации.
"""


def _violates(text: str) -> bool:
    return bool(_BANNED_RE.search(text or ""))


def _sanitize(insight: Insight) -> Insight:
    """If an insight slips prescriptive language, neutralise its framing."""
    # `relevance` is persisted and returned too, so it must be filtered as well.
    if _violates(insight.title) or _violates(insight.body) or _violates(insight.relevance):
        logger.warning("advisor insight tripped banned-phrase filter; softening")
        softened = _BANNED_RE.sub("—", insight.body)
        relevance = _BANNED_RE.sub("—", insight.relevance) if insight.relevance else insight.relevance
        return Insight(
            title="Наблюдение по структуре портфеля",
            category=insight.category,
            body=(softened + " (Текст автоматически приведён к нейтральной форме.)").strip(),
            relevance=relevance,
        )
    return insight


def _portfolio_brief(
    assets: list[AssetItem], total_usd: Decimal | None, geo: dict[str, Any] | None
) -> str:
    lines = [f"Всего активов: {len(assets)}. Капитал ≈ ${total_usd}." if total_usd else
             f"Всего активов: {len(assets)}."]
    for a in assets:
        usd = f"${a.usd_value}" if a.usd_value is not None else "оценка н/д"
        lines.append(
            f"- {a.asset_type}: {a.amount} {a.symbol or a.currency or ''} "
            f"({usd}){f', {a.country}' if a.country else ''}"
        )
    if geo and geo.get("exposure"):
        exp = "; ".join(
            f"{r['country']} {r['pct']}%" for r in geo["exposure"] if r.get("pct")
        )
        if exp:
            lines.append(f"Страновое распределение: {exp}.")
    return "\n".join(lines)


async def generate_advice(
    assets: list[AssetItem],
    total_usd: Decimal | None = None,
    geo: dict[str, Any] | None = None,
    news: list[dict[str, Any]] | None = None,
    language: str = "ru",
) -> list[dict[str, Any]]:
    """Produce a list of sanitized insight dicts (DISCLAIMER added at save time)."""
    if not assets:
        return []

    if settings.is_demo:
        from core.demo import demo_advice
        return [
            {**ins, "disclaimer": DISCLAIMER}
            for ins in demo_advice(assets, total_usd, geo, news, language)
        ]

    system = _SYSTEM
    if language == "en":
        system += "\n\nIMPORTANT: write all insights in ENGLISH."
    model = structured(ModelRole.ADVISOR, AdvisorOutput)
    brief = _portfolio_brief(assets, total_usd, geo)
    if news:
        brief += "\n\nНовости по теме:\n" + "\n".join(
            f"- {n.get('title')}" for n in news[:5]
        )

    result: AdvisorOutput = await model.ainvoke(
        [("system", system), ("human", brief)]
    )

    insights = [_sanitize(i) for i in result.insights]
    return [
        {
            "title": i.title,
            "category": i.category,
            "body": i.body,
            "relevance": i.relevance,
            "disclaimer": DISCLAIMER,
        }
        for i in insights
    ]
