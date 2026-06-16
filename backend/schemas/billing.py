"""Billing API schemas (Pricing + Subscription/Billing screens)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class PlanOut(BaseModel):
    name: str
    label: str
    max_snapshots_per_month: Optional[int] = None
    max_assets_per_snapshot: Optional[int] = None
    advice_per_week: Optional[int] = None
    can_use_scenarios: bool
    can_export: bool


class SubscriptionOut(BaseModel):
    plan: str
    label: str
    expires_at: Optional[str] = None
    is_active_paid: bool
    has_stripe_customer: bool


class CheckoutRequest(BaseModel):
    provider: str = "stripe"  # "stripe" | "coingate"
    plan: str = "pro"  # "pro" | "business"
    interval: str = "monthly"  # "monthly" | "yearly"


class CheckoutResponse(BaseModel):
    url: str
    provider: str
    reference: Optional[str] = None
