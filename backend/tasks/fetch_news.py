"""Celery task: refresh the shared news cache (``news_items``) every 6h.

Builds a single set of ``AssetItem``s representing what users currently hold
(distinct crypto symbols / currencies / countries across recent confirmed
snapshots), runs the news aggregator once, and upserts the ranked headlines into
the cache table. The advisor graph can then read warm rows instead of hitting
NewsAPI per request.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from agents.state import AssetItem
from core.db import SessionLocal
from models.asset import Asset
from models.news import NewsItem
from models.snapshot import Snapshot
from services.news import aggregate_news
from tasks.celery_app import celery
from tasks.runner import run

logger = logging.getLogger("kapital.tasks.news")

_LOOKBACK_DAYS = 30
_MAX_ASSETS = 200


async def _distinct_holdings() -> list[AssetItem]:
    """Representative holdings across recently-confirmed snapshots."""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=_LOOKBACK_DAYS)
    async with SessionLocal() as db:
        rows = list(
            await db.scalars(
                select(Asset)
                .join(Snapshot, Snapshot.id == Asset.snapshot_id)
                .where(Snapshot.is_confirmed.is_(True), Snapshot.created_at >= since)
                .limit(_MAX_ASSETS)
            )
        )

    # Collapse to distinct (type, symbol/currency, country) so the term set is small.
    seen: set[tuple] = set()
    items: list[AssetItem] = []
    for a in rows:
        key = (a.asset_type, (a.symbol or a.currency or ""), a.country or "")
        if key in seen:
            continue
        seen.add(key)
        items.append(
            AssetItem(
                asset_type=a.asset_type,
                amount=a.amount or 0,
                currency=a.currency or "USD",
                country=a.country,
                symbol=a.symbol,
            )
        )
    return items


async def _refresh() -> int:
    holdings = await _distinct_holdings()
    if not holdings:
        logger.info("refresh_news: no recent holdings, nothing to fetch")
        return 0

    headlines = await aggregate_news(holdings)
    if not headlines:
        logger.info("refresh_news: aggregator returned no items")
        return 0

    inserted = 0
    async with SessionLocal() as db:
        for h in headlines:
            title = h.get("title")
            if not title:
                continue
            url = h.get("url")
            # Dedup on url when present, else on title.
            exists = await db.scalar(
                select(NewsItem.id).where(
                    NewsItem.url == url if url else NewsItem.title == title
                )
            )
            if exists:
                continue
            db.add(
                NewsItem(
                    title=title,
                    summary=h.get("summary"),
                    url=url,
                    source=h.get("source"),
                    category=h.get("category"),
                    tags=h.get("tags") or [],
                )
            )
            inserted += 1
        await db.commit()
    logger.info("refresh_news: inserted %d new headlines", inserted)
    return inserted


@celery.task(name="tasks.fetch_news.refresh_news")
def refresh_news() -> int:
    return run(_refresh())
