"""Portable column types shared across models.

Goals:
- Same model code runs on SQLite (dev) and PostgreSQL (prod).
- Transparent field-level encryption for sensitive strings (NFR-001).
"""
from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.types import TypeDecorator

from core.crypto import decrypt, encrypt


class EncryptedString(TypeDecorator):
    """Transparently encrypts/decrypts a string column with Fernet.

    No-op when FERNET_KEY is unset (dev convenience). Stored as Text.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[override]
        return encrypt(value)

    def process_result_value(self, value, dialect):  # type: ignore[override]
        return decrypt(value)


# Convenience aliases for readability in models.
ShortStr = String(255)
