"""News Aggregator service (Agent #9).

Fetches headlines relevant to what the user actually holds (crypto symbols,
currencies, countries), then has an LLM (NEWS_RANK role) keep only the most
portfolio-relevant ones.

Fully optional and degrades to an empty list when:
  - no NEWS_API_KEY is configured, or
  - the network / LLM is unavailable.

So the advisor pipeline never blocks on news.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from agents.state import AssetItem
from core.config import settings
from core.llm import ModelRole, structured

logger = logging.getLogger("kapital.news")

_HTTP_TIMEOUT = httpx.Timeout(10.0)
_MAX_RAW = 20
_MAX_RANKED = 5


class RankedHeadline(BaseModel):
    title: str
    source: str | None = None
    url: str | None = None
    relevance: str = Field(description="Why this matters to the portfolio")


class RankedNews(BaseModel):
    items: list[RankedHeadline] = Field(default_factory=list)


# Terms flow into the NEWS_RANK system prompt, so they must be sanitized to a
# safe charset to prevent prompt injection via user-controlled country/currency.
_SAFE_TERM_RE = re.compile(r"[^A-Za-z0-9 \-]")
_MAX_TERM_LEN = 32


def _safe_term(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _SAFE_TERM_RE.sub("", str(value)).strip()[:_MAX_TERM_LEN].strip()
    return cleaned or None


def _portfolio_terms(assets: list[AssetItem]) -> list[str]:
    terms: set[str] = set()
    for a in assets:
        if a.asset_type == "crypto" and (a.symbol or a.currency):
            terms.add((a.symbol or a.currency).upper())
        if a.country:
            terms.add(a.country)
        if a.currency and a.asset_type != "crypto":
            terms.add(a.currency)
    return sorted(t for t in (_safe_term(t) for t in terms) if t)


async def _fetch_newsapi(terms: list[str]) -> list[dict[str, Any]]:
    if not settings.news_api_key or not terms:
        return []
    query = " OR ".join(terms[:6])
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": _MAX_RAW,
                    "apiKey": settings.news_api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        logger.warning("NewsAPI fetch failed: %s", e)
        return []

    return [
        {
            "title": a.get("title"),
            "source": (a.get("source") or {}).get("name"),
            "url": a.get("url"),
        }
        for a in data.get("articles", [])
        if a.get("title")
    ]


async def aggregate_news(assets: list[AssetItem]) -> list[dict[str, Any]]:
    terms = _portfolio_terms(assets)
    raw = await _fetch_newsapi(terms)
    if not raw:
        return []

    # LLM relevance ranking (optional).
    try:
        model = structured(ModelRole.NEWS_RANK, RankedNews)
        headlines = "\n".join(f"- {h['title']} ({h.get('source')})" for h in raw)
        result: RankedNews = await model.ainvoke(
            [
                (
                    "system",
                    "Keep only the headlines most relevant to a portfolio holding: "
                    f"{', '.join(terms)}. Return at most {_MAX_RANKED}. Be concise.",
                ),
                ("human", headlines),
            ]
        )
        return [i.model_dump() for i in result.items][:_MAX_RANKED]
    except Exception as e:  # noqa: BLE001 — ranking is best-effort
        logger.warning("news ranking skipped: %s", e)
        return raw[:_MAX_RANKED]
