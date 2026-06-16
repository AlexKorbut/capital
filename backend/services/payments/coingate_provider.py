"""CoinGate payment provider — crypto (USDT/BTC) prepaid access (plan §3.1).

Crypto payments are one-shot, so we don't model them as a recurring
subscription. Instead each paid CoinGate order grants a fixed **prepaid Pro
period** (``crypto_pro_months``): the webhook pushes ``sub_expires_at`` forward
from the later of "now" and the user's current expiry, stacking renewals.

Webhook authenticity: CoinGate signs callbacks with an HMAC-SHA256 of the raw
body keyed by the per-app callback secret, sent in ``X-CoinGate-Signature``. We
also pin the order to a user via the ``order_id`` we set at creation.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

from core.config import settings
from services.payments.base import CheckoutSession, SubscriptionUpdate

_SANDBOX_BASE = "https://api-sandbox.coingate.com/v2"
_LIVE_BASE = "https://api.coingate.com/v2"


def _api_base() -> str:
    return _SANDBOX_BASE if settings.coingate_sandbox else _LIVE_BASE


@dataclass
class CoinGateProvider:
    name: str = "coingate"

    # --- Checkout -------------------------------------------------------------

    async def create_checkout(
        self,
        *,
        user: Any,
        plan: str = "pro",
        interval: str = "monthly",
    ) -> CheckoutSession:
        # interval is ignored for crypto — we always sell the prepaid block.
        months = settings.crypto_pro_months
        price = settings.crypto_pro_price_usd
        base = settings.public_url.rstrip("/")
        # order_id carries the user + plan + month-count back through the webhook.
        order_id = f"{user.id}:pro:{months}"

        payload = {
            "order_id": order_id,
            "price_amount": price,
            "price_currency": "USD",
            "receive_currency": "USDT",
            "title": f"КАПИТАЛЬ Pro — {months} мес.",
            "callback_url": f"{base.replace('5173', '8000')}/api/v1/webhooks/coingate",
            "success_url": f"{base}/settings?checkout=success",
            "cancel_url": f"{base}/pricing?checkout=cancelled",
        }
        headers = {"Authorization": f"Token {settings.coingate_api_token}"}

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{_api_base()}/orders", json=payload, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()

        return CheckoutSession(
            url=data["payment_url"], provider=self.name, reference=str(data.get("id"))
        )

    # --- Webhook --------------------------------------------------------------

    def verify_and_parse(
        self,
        *,
        payload: bytes,
        headers: dict[str, str],
    ) -> SubscriptionUpdate:
        secret = settings.coingate_webhook_secret
        sig = headers.get("x-coingate-signature") or headers.get("X-CoinGate-Signature")
        if secret:
            expected = hmac.new(
                secret.encode("utf-8"), payload, hashlib.sha256
            ).hexdigest()
            if not sig or not hmac.compare_digest(expected, sig):
                raise ValueError("Invalid CoinGate signature")

        data = _form_or_json(payload)
        status_raw = (data.get("status") or "").lower()
        order_id = data.get("order_id") or ""
        token = data.get("token")  # CoinGate order token, unique → idempotency

        user_id, plan, months = _split_order_id(order_id)

        if status_raw == "paid":
            expires = _now() + timedelta(days=30 * max(months, 1))
            return SubscriptionUpdate(
                user_id=user_id,
                status="active",
                plan=plan or "pro",
                expires_at=expires,
                event_type="created",
                provider=self.name,
                provider_ref=token or order_id,
                amount_usd=_amount(data),
                currency="USD",
                raw={"status": status_raw},
            )

        if status_raw in ("canceled", "cancelled", "expired", "invalid"):
            return SubscriptionUpdate(
                user_id=user_id,
                status="failed",
                plan=None,
                event_type="failed",
                provider=self.name,
                provider_ref=token or order_id,
                raw={"status": status_raw},
            )

        return SubscriptionUpdate(
            user_id=None,
            status="ignored",
            provider=self.name,
            provider_ref=token or order_id,
        )


# --- helpers ------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _split_order_id(order_id: str) -> tuple[str | None, str | None, int]:
    parts = (order_id or "").split(":")
    user_id = parts[0] or None if parts else None
    plan = parts[1] if len(parts) > 1 else "pro"
    try:
        months = int(parts[2]) if len(parts) > 2 else settings.crypto_pro_months
    except ValueError:
        months = settings.crypto_pro_months
    return user_id, plan, months


def _amount(data: dict[str, Any]) -> str | None:
    raw = data.get("price_amount") or data.get("pay_amount")
    if raw is None:
        return None
    try:
        return str(Decimal(str(raw)).quantize(Decimal("0.01")))
    except Exception:  # noqa: BLE001 - defensive parse
        return None


def _form_or_json(payload: bytes) -> dict[str, Any]:
    import json
    from urllib.parse import parse_qs

    text = payload.decode("utf-8", errors="replace").strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return {k: v[0] for k, v in parse_qs(text).items()}
