"""Auth request/response schemas (Pydantic v2)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: Optional[str] = None
    base_currency: str = Field(default="USD", min_length=3, max_length=3)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    # Required only when the account has 2FA enabled (TOTP or a recovery code).
    code: Optional[str] = None


class RefreshRequest(BaseModel):
    # Optional: the refresh token normally arrives in an httpOnly cookie. The
    # body field is kept for backward compatibility / non-browser clients.
    refresh_token: Optional[str] = None


class EmailOnlyRequest(BaseModel):
    email: EmailStr


class TokenOnlyRequest(BaseModel):
    token: str


class PasswordResetRequest(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


class MessageResponse(BaseModel):
    message: str


class TokenPair(BaseModel):
    access_token: str
    # Empty in responses: the refresh token is delivered as an httpOnly cookie,
    # never in the JS-readable body (so XSS can't lift the long-lived token).
    refresh_token: str = ""
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    name: Optional[str] = None
    base_currency: str
    subscription: str
    sub_expires_at: Optional[datetime] = None
    email_verified: bool
    created_at: datetime


class AuthResponse(BaseModel):
    user: UserOut
    tokens: TokenPair


# --- Two-factor auth ----------------------------------------------------------


class TwoFASetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class TwoFAEnableRequest(BaseModel):
    code: str


class TwoFAEnableResponse(BaseModel):
    recovery_codes: list[str]


class CodeRequest(BaseModel):
    code: str


class TwoFAStatus(BaseModel):
    enabled: bool
    recovery_codes_left: int = 0
