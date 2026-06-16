"""Shared FastAPI dependencies (current-user resolution)."""
from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.security import decode_token
from models.user import User

_bearer = HTTPBearer(auto_error=False)

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if creds is None:
        raise _CREDENTIALS_EXC
    try:
        payload = decode_token(creds.credentials, expected_type="access")
        user_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise _CREDENTIALS_EXC

    user = await db.get(User, user_id)
    if user is None:
        raise _CREDENTIALS_EXC
    # "Log out everywhere": tokens issued before a token_version bump are dead.
    if int(payload.get("tv", 0)) != int(user.token_version or 0):
        raise _CREDENTIALS_EXC
    return user
