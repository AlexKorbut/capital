"""Shared FastAPI dependencies (current-user resolution).

Two credential kinds are accepted in ``Authorization: Bearer <value>``:

* a JWT access token issued by ``/auth/login`` (browser sessions), or
* a personal API token ``kp_…`` created in Settings (machine clients such as
  Jarvis; see ``core.tokens`` / ``models.api_token``).

Resolving the user also publishes the user's AI configuration (BYOK) to the
request context so LLM services pick the right keys (``core.llm``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.llm import set_user_ai, user_ai_from_settings
from core.security import decode_token
from core.tokens import hash_token, looks_like_token
from models.api_token import ApiToken
from models.user import User

_bearer = HTTPBearer(auto_error=False)

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

# ``last_used_at`` is refreshed at most this often to keep hot API clients
# from writing a row on every request.
_LAST_USED_REFRESH = timedelta(seconds=60)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def user_from_api_token(db: AsyncSession, token: str) -> User | None:
    """Resolve an active personal API token to its user (None if unknown/revoked)."""
    row = await db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
    if row is None or row.revoked_at is not None:
        return None
    user = await db.get(User, row.user_id)
    if user is None:
        return None
    now = datetime.now(timezone.utc)
    last = _as_utc(row.last_used_at)
    if last is None or now - last >= _LAST_USED_REFRESH:
        row.last_used_at = now
        await db.commit()
    return user


async def _user_from_jwt(db: AsyncSession, token: str) -> User | None:
    try:
        payload = decode_token(token, expected_type="access")
        user_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None
    user = await db.get(User, user_id)
    if user is None:
        return None
    # "Log out everywhere": tokens issued before a token_version bump are dead.
    if int(payload.get("tv", 0)) != int(user.token_version or 0):
        return None
    return user


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if creds is None:
        raise _CREDENTIALS_EXC
    value = creds.credentials
    if looks_like_token(value):
        user = await user_from_api_token(db, value)
    else:
        user = await _user_from_jwt(db, value)
    if user is None:
        raise _CREDENTIALS_EXC
    # BYOK: make the user's AI mode/keys visible to LLM services for this request.
    set_user_ai(user_ai_from_settings(user.settings))
    return user
