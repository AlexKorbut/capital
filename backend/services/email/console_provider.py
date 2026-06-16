"""Console email provider — dev default. Logs the message instead of sending."""
from __future__ import annotations

import logging

from services.email.base import EmailMessage

logger = logging.getLogger("kapital.email")


class ConsoleProvider:
    name = "console"

    async def send(self, message: EmailMessage) -> None:
        logger.info(
            "\n--- EMAIL (console) ---\nTo: %s\nSubject: %s\n\n%s\n-----------------------",
            message.to,
            message.subject,
            message.text,
        )
