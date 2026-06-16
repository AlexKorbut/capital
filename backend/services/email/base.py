"""Email provider Protocol + message value object."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str | None = None


@runtime_checkable
class EmailProvider(Protocol):
    name: str

    async def send(self, message: EmailMessage) -> None:
        """Deliver the message. Implementations must not raise on transient
        failures unless the caller needs to know — verification/reset flows
        deliberately swallow send errors to avoid leaking account existence."""
        ...
