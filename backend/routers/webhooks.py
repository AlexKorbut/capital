"""Payment webhooks — the single source of truth for subscription state.

Each endpoint reads the **raw** request body (signature verification needs the
exact bytes), verifies the provider signature, normalizes the event, and applies
it idempotently. We always return 200 for a verified-but-uninteresting event so
the provider does not keep retrying; only a bad signature returns 400.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from services.payments import get_provider
from services.payments.apply import apply_subscription_update

logger = logging.getLogger("kapital.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def _handle(provider_name: str, request: Request, db: AsyncSession) -> dict:
    payload = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    provider = get_provider(provider_name)

    try:
        update = provider.verify_and_parse(payload=payload, headers=headers)
    except Exception as exc:  # noqa: BLE001 - any verify failure ⇒ 400
        logger.warning("%s webhook rejected: %s", provider_name, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature"
        ) from exc

    changed = await apply_subscription_update(db, update)
    logger.info(
        "%s webhook ok: status=%s changed=%s ref=%s",
        provider_name,
        update.status,
        changed,
        update.provider_ref,
    )
    return {"received": True, "applied": changed}


@router.post("/stripe")
async def stripe_webhook(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    return await _handle("stripe", request, db)


@router.post("/coingate")
async def coingate_webhook(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    return await _handle("coingate", request, db)
