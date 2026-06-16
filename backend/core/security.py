"""Auth primitives: password hashing (bcrypt) and JWT access/refresh tokens."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
from jose import JWTError, jwt

from core.config import settings

TokenType = Literal["access", "refresh"]


# --- Passwords -----------------------------------------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --- JWT -----------------------------------------------------------------
def _create_token(
    subject: str, token_type: TokenType, expires: timedelta, token_version: int = 0
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "tv": token_version,  # invalidated by "log out everywhere"
        "iat": now,
        "exp": now + expires,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str, token_version: int = 0) -> str:
    return _create_token(
        subject, "access", timedelta(minutes=settings.access_token_ttl_min),
        token_version,
    )


def create_refresh_token(subject: str, token_version: int = 0) -> str:
    return _create_token(
        subject, "refresh", timedelta(days=settings.refresh_token_ttl_days),
        token_version,
    )


def decode_token(token: str, expected_type: TokenType | None = None) -> dict[str, Any]:
    """Decode and validate a JWT. Raises JWTError on failure/type mismatch."""
    payload = jwt.decode(
        token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    if expected_type and payload.get("type") != expected_type:
        raise JWTError(f"Expected {expected_type} token, got {payload.get('type')}")
    return payload


# --- Purpose-scoped tokens (email verification / password reset) ---------
def create_email_token(subject: str, purpose: str, expires: timedelta) -> str:
    """Signed, short-lived token carrying a ``purpose`` claim.

    Distinct from access/refresh tokens: it can't be used as a bearer token
    (``type`` is unset) and is bound to a single purpose string.
    """
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "purpose": purpose,
        "iat": now,
        "exp": now + expires,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_email_token(token: str, purpose: str) -> str:
    """Validate a purpose-scoped token and return its subject. Raises JWTError."""
    payload = jwt.decode(
        token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    if payload.get("purpose") != purpose:
        raise JWTError("Token purpose mismatch")
    sub = payload.get("sub")
    if not sub:
        raise JWTError("Token missing subject")
    return str(sub)


def create_verification_token(subject: str) -> str:
    return create_email_token(
        subject, "email_verify", timedelta(hours=settings.verify_token_ttl_hours)
    )


def create_password_reset_token(subject: str) -> str:
    return create_email_token(
        subject, "password_reset", timedelta(minutes=settings.reset_token_ttl_min)
    )
