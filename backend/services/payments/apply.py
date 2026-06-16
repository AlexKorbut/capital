"""Apply a verified :class:`SubscriptionUpdate` to a user (source of truth).

Webhooks are authoritative; the client never sets plan state. This module is
the single place that mutates ``users.subscription`` / ``sub_expires_at`` and
records a row in ``subscription_events``. It is **idempotent**: replaying the
same provider event (same ``provider`` + ``provider_ref``) is a no-op.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.billing import SubscriptionEvent
from models.user import User
from services.payments.base import SubscriptionUpdate


async def _already_processed(db: AsyncSession, upd: SubscriptionUpdate) -> bool:
    if not upd.provider_ref:
        return False
    existing = await db.scalar(
        select(SubscriptionEvent.id).where(
            SubscriptionEvent.payment_provider == upd.provider,
            SubscriptionEvent.provider_ref == upd.provider_ref,
        )
    )
    return existing is not None


async def _find_user(db: AsyncSession, upd: SubscriptionUpdate) -> Optional[User]:
    if upd.user_id:
        try:
            import uuid

            user = await db.get(User, uuid.UUID(str(upd.user_id)))
            if user is not None:
                return user
        except (ValueError, TypeError):
            pass
    if upd.stripe_customer_id:
        return await db.scalar(
            select(User).where(User.stripe_customer_id == upd.stripe_customer_id)
        )
    return None


def _max_expiry(current: datetime | None, incoming: datetime | None) -> datetime | None:
    """Stack prepaid periods: never shorten an existing paid window."""
    if incoming is None:
        return current
    if current is None:
        return incoming
    cur = current if current.tzinfo else current.replace(tzinfo=timezone.utc)
    inc = incoming if incoming.tzinfo else incoming.replace(tzinfo=timezone.utc)
    return max(cur, inc)


async def apply_subscription_update(
    db: AsyncSession, upd: SubscriptionUpdate
) -> bool:
    """Apply ``upd`` to the matching user. Returns True if state changed.

    Ignored events and duplicates return False without side effects.
    """
    if upd.is_ignored:
        return False
    if await _already_processed(db, upd):
        return False

    user = await _find_user(db, upd)
    if user is None:
        return False

    # Bind the Stripe customer id the first time we see it.
    if upd.stripe_customer_id and not user.stripe_customer_id:
        user.stripe_customer_id = upd.stripe_customer_id

    if upd.status == "active":
        if upd.plan and upd.plan != "free":
            user.subscription = upd.plan
        user.sub_expires_at = _max_expiry(user.sub_expires_at, upd.expires_at)
    elif upd.status == "cancelled":
        if upd.plan == "free":
            # Hard cancel (subscription.deleted): degrade immediately.
            user.subscription = "free"
            user.sub_expires_at = None
        else:
            # Soft cancel: let the paid window lapse on its own.
            if upd.expires_at is not None:
                user.sub_expires_at = upd.expires_at
    # status == "failed": record only, no plan change.

    db.add(
        SubscriptionEvent(
            user_id=user.id,
            event_type=upd.event_type,
            plan=upd.plan or user.subscription,
            amount_usd=_as_decimal(upd.amount_usd),
            currency=upd.currency,
            payment_provider=upd.provider,
            provider_ref=upd.provider_ref,
        )
    )
    await db.commit()

    # Receipt on a successful payment (best-effort; never breaks the webhook).
    if upd.status == "active" and upd.amount_usd:
        try:
            from services.email import send_receipt_email

            await send_receipt_email(
                to=user.email,
                plan=user.subscription,
                amount=upd.amount_usd,
                currency=upd.currency,
                expires_at=(
                    user.sub_expires_at.date().isoformat()
                    if user.sub_expires_at
                    else None
                ),
            )
        except Exception:  # noqa: BLE001 — delivery must not fail the webhook
            pass
    return True


def _as_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None
