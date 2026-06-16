"""Reminder & weekly-digest jobs (Feature #9).

Run by the dev APScheduler (and Celery beat in prod). Both open their own DB
session and never raise out — a scheduler job must not crash the loop.

Opt-out: ``user.settings["no_emails"] = true`` suppresses both.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from core.db import SessionLocal
from models.snapshot import Snapshot
from models.user import User
from services import email as email_service
from services import returns as returns_service

logger = logging.getLogger("kapital.digest")

STALE_DAYS = 7


def _opted_out(user: User) -> bool:
    return bool((user.settings or {}).get("no_emails"))


async def run_update_reminders(stale_days: int = STALE_DAYS) -> int:
    """Email users whose latest snapshot is older than ``stale_days``. Returns count."""
    sent = 0
    async with SessionLocal() as db:
        users = list(await db.scalars(select(User)))
        for user in users:
            if _opted_out(user) or not user.email_verified:
                continue
            last = await db.scalar(
                select(Snapshot.created_at)
                .where(Snapshot.user_id == user.id, Snapshot.is_confirmed.is_(True))
                .order_by(Snapshot.created_at.desc())
                .limit(1)
            )
            if last is None:
                continue  # never added anything — onboarding handles that
            last_aware = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            days = (datetime.now(timezone.utc) - last_aware).days
            if days >= stale_days:
                try:
                    await email_service.send_reminder_email(
                        to=user.email, name=user.name, days=days,
                        lang=(user.settings or {}).get("lang", "ru"),
                    )
                    sent += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("reminder send failed for %s: %s", user.email, e)
    logger.info("update reminders sent: %d", sent)
    return sent


async def run_weekly_digest() -> int:
    """Email a 7-day net-worth digest to users with a portfolio. Returns count."""
    sent = 0
    async with SessionLocal() as db:
        users = list(await db.scalars(select(User)))
        for user in users:
            if _opted_out(user) or not user.email_verified:
                continue
            data = await returns_service.compute_returns(db, user.id)
            if not data or data.get("current_usd") is None:
                continue
            window = next(
                (w for w in data["windows"] if w["key"] in ("7d", "30d", "all")), None
            )
            change = window["change_usd"] if window else "0"
            pct = window["change_pct"] if window else None
            try:
                await email_service.send_digest_email(
                    to=user.email,
                    name=user.name,
                    current_usd=str(data["current_usd"]),
                    change_usd=str(change),
                    change_pct=pct,
                    lang=(user.settings or {}).get("lang", "ru"),
                )
                sent += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("digest send failed for %s: %s", user.email, e)
    logger.info("weekly digests sent: %d", sent)
    return sent
