"""Payment providers — Stripe (cards) + CoinGate (crypto), behind one Protocol.

Providers are constructed lazily so that importing this package never requires
vendor credentials to be present (handy for tests and the dev console flow).
"""
from __future__ import annotations

from services.payments.base import (
    CheckoutSession,
    PaymentProvider,
    SubscriptionUpdate,
)

__all__ = [
    "CheckoutSession",
    "PaymentProvider",
    "SubscriptionUpdate",
    "get_provider",
]


def get_provider(name: str) -> PaymentProvider:
    """Return a provider instance by name ("stripe" | "coingate")."""
    key = (name or "").lower()
    if key == "stripe":
        from services.payments.stripe_provider import StripeProvider

        return StripeProvider()
    if key == "coingate":
        from services.payments.coingate_provider import CoinGateProvider

        return CoinGateProvider()
    raise ValueError(f"Unknown payment provider: {name!r}")
