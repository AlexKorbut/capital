"""Trusted-contact / dead-man's-switch (Feature #10).

A user names a trusted contact and an inactivity threshold. If they don't sign in
for that many days, the contact is emailed a brief summary (no account access is
granted). Configuration lives in ``user.settings``:

    beneficiary_email     str    — trusted contact
    beneficiary_days      int    — inactivity threshold (days)
    beneficiary_notified  bool   — set true after a notice is sent (reset on login)

A daily scheduler job (dev APScheduler / prod Celery beat) runs the check.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.db import SessionLocal
from models.user import User
from services import email as email_service
from services import portfolio_read
from services.userscan import iter_users

logger = logging.getLogger("kapital.inheritance")


def beneficiary_config(user: User) -> dict:
    s = user.settings or {}
    return {
        "email": s.get("beneficiary_email"),
        "days": int(s.get("beneficiary_days") or 0),
        "notified": bool(s.get("beneficiary_notified")),
    }


async def run_inheritance_check() -> int:
    """Notify trusted contacts of inactive owners. Returns notices sent."""
    sent = 0
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        async for user in iter_users(db):
            cfg = beneficiary_config(user)
            if not cfg["email"] or cfg["days"] <= 0 or cfg["notified"]:
                continue
            last = user.last_active_at
            if last is None:
                continue
            last_aware = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            days = (now - last_aware).days
            if days < cfg["days"]:
                continue

            snap = await portfolio_read.latest_snapshot(db, user.id)
            current = str(snap.total_usd) if snap and snap.total_usd is not None else None
            try:
                await email_service.send_inheritance_email(
                    to=cfg["email"],
                    owner_name=user.name,
                    owner_email=user.email,
                    current_usd=current,
                    days=days,
                    lang=(user.settings or {}).get("lang", "ru"),
                )
            except Exception as e:  # noqa: BLE001 — transient failure: retry next run
                logger.warning(
                    "inheritance notice failed for %s (will retry): %s", user.email, e
                )
                continue
            # Mark notified ONLY after a successful send, so a transient failure is
            # retried rather than silently swallowed. A crash in the tiny
            # send→commit window risks at most one duplicate notice — acceptable
            # versus permanently missing a dead-man's-switch alert.
            s = dict(user.settings or {})
            s["beneficiary_notified"] = True
            user.settings = s
            await db.commit()
            sent += 1
    logger.info("inheritance notices sent: %d", sent)
    return sent
