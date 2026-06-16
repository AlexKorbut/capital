"""TOTP (RFC 6238) two-factor auth — pure stdlib, no extra dependency.

Compatible with Google Authenticator / Authy / 1Password etc. (SHA-1, 6 digits,
30-second period). We generate a base32 secret, build an ``otpauth://`` URI for
enrolment, and verify 6-digit codes with a ±1 step window for clock drift.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

_DIGITS = 6
_PERIOD = 30
_ISSUER = "KAPITAL"


def generate_secret() -> str:
    """Random base32 secret (160-bit), no padding (authenticator-friendly)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _hotp(secret_b32: str, counter: int, digits: int = _DIGITS) -> str:
    pad = "=" * ((8 - len(secret_b32) % 8) % 8)
    key = base64.b32decode(secret_b32 + pad, casefold=True)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def verify_totp(secret: str, code: str, window: int = 1, t: float | None = None) -> bool:
    """True if ``code`` matches the secret within ±``window`` 30s steps."""
    if not secret or not code:
        return False
    code = code.strip().replace(" ", "")
    if not code.isdigit():
        return False
    counter = int((t if t is not None else time.time()) // _PERIOD)
    return any(
        hmac.compare_digest(_hotp(secret, counter + off), code)
        for off in range(-window, window + 1)
    )


def provisioning_uri(secret: str, account: str, issuer: str = _ISSUER) -> str:
    """otpauth:// URI to render as a QR or paste into an authenticator app."""
    label = urllib.parse.quote(f"{issuer}:{account}")
    query = urllib.parse.urlencode(
        {"secret": secret, "issuer": issuer, "digits": _DIGITS, "period": _PERIOD}
    )
    return f"otpauth://totp/{label}?{query}"


# --- Recovery codes -----------------------------------------------------------


def generate_recovery_codes(n: int = 10) -> list[str]:
    """Human-friendly one-time backup codes, e.g. ``a1b2-c3d4``."""
    return [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()
