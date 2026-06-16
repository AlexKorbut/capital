"""Billing router — pricing, current subscription, checkout + portal.

Money state is never mutated here; checkout only mints a hosted redirect URL.
The actual plan change lands via the webhooks router once the provider confirms
payment (see ``routers/webhooks.py``).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from core.deps import get_current_user
from core.tiers import PLANS, limits_for, plan_of
from models.user import User
from schemas.billing import (
    CheckoutRequest,
    CheckoutResponse,
    PlanOut,
    SubscriptionOut,
)
from services.payments import get_provider

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans", response_model=list[PlanOut])
async def list_plans() -> list[PlanOut]:
    """Public plan catalogue for the Pricing page."""
    return [
        PlanOut(
            name=p.name,
            label=p.label,
            max_snapshots_per_month=p.max_snapshots_per_month,
            max_assets_per_snapshot=p.max_assets_per_snapshot,
            advice_per_week=p.advice_per_week,
            can_use_scenarios=p.can_use_scenarios,
            can_export=p.can_export,
        )
        for p in PLANS.values()
    ]


@router.get("/subscription", response_model=SubscriptionOut)
async def my_subscription(
    current: User = Depends(get_current_user),
) -> SubscriptionOut:
    effective = plan_of(current)
    limits = limits_for(current)
    return SubscriptionOut(
        plan=effective,
        label=limits.label,
        expires_at=current.sub_expires_at.isoformat() if current.sub_expires_at else None,
        is_active_paid=effective != "free",
        has_stripe_customer=bool(current.stripe_customer_id),
    )


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    body: CheckoutRequest,
    current: User = Depends(get_current_user),
) -> CheckoutResponse:
    if body.plan not in ("pro", "business"):
        raise HTTPException(status_code=400, detail="Unknown plan")
    try:
        provider = get_provider(body.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        session = await provider.create_checkout(
            user=current, plan=body.plan, interval=body.interval
        )
    except ValueError as exc:
        # Misconfiguration (missing price id / customer) — surface clearly.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return CheckoutResponse(
        url=session.url, provider=session.provider, reference=session.reference
    )


@router.post("/portal", response_model=CheckoutResponse)
async def billing_portal(
    current: User = Depends(get_current_user),
) -> CheckoutResponse:
    """Stripe Billing Customer Portal — manage/cancel/change card."""
    from services.payments.stripe_provider import StripeProvider

    if not current.stripe_customer_id:
        raise HTTPException(
            status_code=409, detail="Нет активной подписки Stripe для управления."
        )
    try:
        session = await StripeProvider().create_portal(user=current)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CheckoutResponse(
        url=session.url, provider=session.provider, reference=session.reference
    )
