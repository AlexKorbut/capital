"""Resend email provider (prod). Sends via the Resend HTTP API over httpx."""
from __future__ import annotations

import logging

import httpx

from core.config import settings
from services.email.base import EmailMessage

logger = logging.getLogger("kapital.email")

_RESEND_URL = "https://api.resend.com/emails"


class ResendProvider:
    name = "resend"

    async def send(self, message: EmailMessage) -> None:
        if not settings.resend_api_key:
            logger.warning("Resend API key missing — email to %s not sent", message.to)
            return
        payload = {
            "from": settings.email_from,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
        }
        if message.html:
            payload["html"] = message.html

        headers = {"Authorization": f"Bearer {settings.resend_api_key}"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(_RESEND_URL, json=payload, headers=headers)
                resp.raise_for_status()
        except httpx.HTTPError as exc:  # transient — don't break the request flow
            logger.error("Resend send failed for %s: %s", message.to, exc)
