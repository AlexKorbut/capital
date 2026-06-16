"""Payment provider abstraction (plan §3.1).

A ``PaymentProvider`` hides the difference between Stripe (recurring card
subscriptions) and CoinGate (one-shot crypto → prepaid period) behind one
interface so routers never import a vendor SDK directly. Both providers turn a
verified provider event into a normalized :class:`SubscriptionUpdate`, which the
webhooks router applies to the user as the single source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class CheckoutSession:
    """Where to send the browser to pay."""

    url: str
    provider: str
    reference: str | None = None  # provider session/order id, for our records


@dataclass(frozen=True)
class SubscriptionUpdate:
    """Normalized outcome of a (verified) provider webhook.

    ``apply`` semantics are decided by the webhooks router:
      - ``active``   → set plan + push ``expires_at`` forward (or None=unlimited)
      - ``cancelled``→ let the current period lapse (degrade handled by tiers)
      - ``failed``   → record only
    """

    user_id: str | None
    status: str  # "active" | "cancelled" | "failed" | "ignored"
    plan: str | None = None  # "pro" | "business"
    expires_at: datetime | None = None
    event_type: str = "created"  # created|renewed|cancelled|failed
    provider: str = ""
    provider_ref: str | None = None  # unique event id → idempotency key
    amount_usd: str | None = None
    currency: str | None = None
    stripe_customer_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ignored(self) -> bool:
        return self.status == "ignored"


@runtime_checkable
class PaymentProvider(Protocol):
    """Vendor-agnostic payment surface."""

    name: str

    async def create_checkout(
        self,
        *,
        user: Any,
        plan: str,
        interval: str = "monthly",
    ) -> CheckoutSession:
        """Create a hosted checkout and return the redirect URL."""
        ...

    def verify_and_parse(
        self,
        *,
        payload: bytes,
        headers: dict[str, str],
    ) -> SubscriptionUpdate:
        """Verify the webhook signature and normalize it.

        MUST raise on an invalid signature (callers turn that into 400). A valid
        but uninteresting event returns ``SubscriptionUpdate(status="ignored")``.
        """
        ...
