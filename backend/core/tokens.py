"""Personal API token primitives (pure helpers, no DB access).

Format: ``kp_`` + 43 URL-safe characters (256 bits of entropy). Only the
SHA-256 hex digest is persisted; ``prefix`` is the short visible head used to
tell tokens apart in the settings list.
"""
from __future__ import annotations

import hashlib
import secrets

TOKEN_PREFIX = "kp_"
# How many leading characters of the plaintext are kept for display.
VISIBLE_PREFIX_LEN = 11  # "kp_" + 8


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def visible_prefix(token: str) -> str:
    return token[:VISIBLE_PREFIX_LEN]


def looks_like_token(value: str | None) -> bool:
    return bool(value) and value.startswith(TOKEN_PREFIX)
