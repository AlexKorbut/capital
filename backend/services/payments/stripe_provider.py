"""Stripe payment provider (plan §3.1).

Cards are entered only on Stripe-hosted pages (Checkout / Billing Portal) so we
stay PCI SAQ-A — no card data ever touches our servers. Webhooks are the source
of truth for subscription state; we verify the ``Stripe-Signature`` header and
map events back to a user via the customer id we stored at first checkout.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import stripe

from core.config import settings
from services.payments.base import CheckoutSession, SubscriptionUpdate

_PLAN_PRICE = {
    ("pro", "monthly"): lambda: settings.stripe_price_pro_monthly,
    ("pro", "yearly"): lambda: settings.stripe_price_pro_yearly,
    ("business", "monthly"): lambda: settings.stripe_price_business_monthly,
}


class StripeProvider:
    name = "stripe"

    def __init__(self) -> None:
        stripe.api_key = settings.stripe_secret_key

    # --- Checkout / Portal ----------------------------------------------------

    def _price_id(self, plan: str, interval: str) -> str:
        getter = _PLAN_PRICE.get((plan, interval))
        if getter is None:
            raise ValueError(f"No Stripe price configured for {plan}/{interval}")
        price = getter()
        if not price:
            raise ValueError(f"Stripe price id missing for {plan}/{interval}")
        return price

    async def create_checkout(
        self,
        *,
        user: Any,
        plan: str,
        interval: str = "monthly",
    ) -> CheckoutSession:
        price_id = self._price_id(plan, interval)
        base = settings.public_url.rstrip("/")

        params: dict[str, Any] = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": f"{base}/settings?checkout=success",
            "cancel_url": f"{base}/pricing?checkout=cancelled",
            "client_reference_id": str(user.id),
            "metadata": {"user_id": str(user.id), "plan": plan},
            "subscription_data": {"metadata": {"user_id": str(user.id), "plan": plan}},
            # Idempotency would normally key on user+plan+nonce; Checkout is
            # already safe to re-create, so we let the SDK manage it.
        }
        if getattr(user, "stripe_customer_id", None):
            params["customer"] = user.stripe_customer_id
        else:
            params["customer_email"] = user.email

        session = await _to_thread(stripe.checkout.Session.create, **params)
        return CheckoutSession(url=session.url, provider=self.name, reference=session.id)

    async def create_portal(self, *, user: Any) -> CheckoutSession:
        """Billing Customer Portal — user manages/cancels/changes card."""
        if not getattr(user, "stripe_customer_id", None):
            raise ValueError("No Stripe customer for this user")
        base = settings.public_url.rstrip("/")
        portal = await _to_thread(
            stripe.billing_portal.Session.create,
            customer=user.stripe_customer_id,
            return_url=f"{base}/settings",
        )
        return CheckoutSession(url=portal.url, provider=self.name, reference=portal.id)

    # --- Webhooks -------------------------------------------------------------

    def verify_and_parse(
        self,
        *,
        payload: bytes,
        headers: dict[str, str],
    ) -> SubscriptionUpdate:
        sig = headers.get("stripe-signature") or headers.get("Stripe-Signature")
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig,
            secret=settings.stripe_webhook_secret,
        )
        return self._parse_event(event)

    def _parse_event(self, event: Any) -> SubscriptionUpdate:
        etype = event["type"]
        obj = event["data"]["object"]
        provider_ref = event.get("id")

        if etype == "checkout.session.completed":
            meta = obj.get("metadata") or {}
            return SubscriptionUpdate(
                user_id=obj.get("client_reference_id") or meta.get("user_id"),
                status="active",
                plan=meta.get("plan") or "pro",
                expires_at=None,  # recurring — period managed by Stripe
                event_type="created",
                provider=self.name,
                provider_ref=provider_ref,
                amount_usd=_cents_to_usd(obj.get("amount_total")),
                currency=(obj.get("currency") or "usd").upper(),
                stripe_customer_id=obj.get("customer"),
                raw={"type": etype},
            )

        if etype == "invoice.paid":
            return SubscriptionUpdate(
                user_id=(obj.get("subscription_details") or {}).get("metadata", {}).get("user_id"),
                status="active",
                plan=None,  # keep current plan; just extend
                expires_at=_period_end(obj),
                event_type="renewed",
                provider=self.name,
                provider_ref=provider_ref,
                amount_usd=_cents_to_usd(obj.get("amount_paid")),
                currency=(obj.get("currency") or "usd").upper(),
                stripe_customer_id=obj.get("customer"),
                raw={"type": etype},
            )

        if etype == "customer.subscription.updated":
            meta = obj.get("metadata") or {}
            cancel = bool(obj.get("cancel_at_period_end")) or obj.get("status") in (
                "canceled",
                "unpaid",
                "incomplete_expired",
            )
            return SubscriptionUpdate(
                user_id=meta.get("user_id"),
                status="cancelled" if cancel else "active",
                plan=meta.get("plan"),
                expires_at=_ts(obj.get("current_period_end")),
                event_type="cancelled" if cancel else "renewed",
                provider=self.name,
                provider_ref=provider_ref,
                stripe_customer_id=obj.get("customer"),
                raw={"type": etype},
            )

        if etype == "customer.subscription.deleted":
            meta = obj.get("metadata") or {}
            return SubscriptionUpdate(
                user_id=meta.get("user_id"),
                status="cancelled",
                plan="free",
                expires_at=_ts(obj.get("current_period_end")),
                event_type="cancelled",
                provider=self.name,
                provider_ref=provider_ref,
                stripe_customer_id=obj.get("customer"),
                raw={"type": etype},
            )

        return SubscriptionUpdate(
            user_id=None, status="ignored", provider=self.name, provider_ref=provider_ref
        )


# --- helpers ------------------------------------------------------------------


def _cents_to_usd(cents: Any) -> str | None:
    if cents is None:
        return None
    return str((Decimal(int(cents)) / Decimal(100)).quantize(Decimal("0.01")))


def _ts(epoch: Any) -> datetime | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc)


def _period_end(invoice: Any) -> datetime | None:
    lines = (invoice.get("lines") or {}).get("data") or []
    for line in lines:
        end = (line.get("period") or {}).get("end")
        if end:
            return _ts(end)
    return None


async def _to_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run the (sync) Stripe SDK off the event loop."""
    import anyio

    return await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs))
