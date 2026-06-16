"""Auth router: register / login / refresh / me + email verify + password reset.

Access + refresh JWTs for sessions; short-lived purpose-scoped tokens (emailed)
for verification and password reset. Sensitive endpoints are rate-limited, and
"forgot password" never reveals whether an email exists (anti-enumeration).

NOTE: this module intentionally does NOT use ``from __future__ import annotations``.
slowapi's ``@limiter.limit`` wraps the endpoint with ``functools.wraps``, which copies
``__annotations__`` onto the wrapper. With stringized annotations FastAPI resolves the
body param against slowapi's module globals (not ours), fails to find the Pydantic model,
and mis-reads it as a query param (422). Eager (real-object) annotations avoid that.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.crypto import decrypt, encrypt
from core.db import get_db
from core.deps import get_current_user
from core.ratelimit import AUTH_LIMIT, FORGOT_LIMIT, limiter
from core.totp import (
    generate_recovery_codes,
    generate_secret,
    hash_recovery_code,
    provisioning_uri,
    verify_totp,
)
from core.security import (
    create_access_token,
    create_password_reset_token,
    create_refresh_token,
    create_verification_token,
    decode_email_token,
    decode_token,
    hash_password,
    verify_password,
)
from jose import JWTError
from models.user import User
from schemas.auth import (
    AuthResponse,
    CodeRequest,
    EmailOnlyRequest,
    LoginRequest,
    MessageResponse,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    TokenOnlyRequest,
    TokenPair,
    TwoFAEnableRequest,
    TwoFAEnableResponse,
    TwoFASetupResponse,
    TwoFAStatus,
    UserOut,
)
from services.email import (
    send_password_reset_email,
    send_verification_email,
    send_welcome_email,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens_for(user: User) -> TokenPair:
    sub = str(user.id)
    tv = int(user.token_version or 0)
    return TokenPair(
        access_token=create_access_token(sub, tv),
        refresh_token=create_refresh_token(sub, tv),
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(AUTH_LIMIT)
async def register(
    request: Request, body: RegisterRequest, db: AsyncSession = Depends(get_db)
) -> AuthResponse:
    existing = await db.scalar(select(User).where(User.email == body.email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        base_currency=body.base_currency.upper(),
    )
    db.add(user)
    await db.flush()  # populate user.id before building response
    await db.refresh(user)

    # Fire the verification email (best-effort; never blocks registration).
    try:
        await send_verification_email(
            to=user.email, token=create_verification_token(str(user.id))
        )
    except Exception:  # noqa: BLE001 - delivery must not fail signup
        pass

    return AuthResponse(user=UserOut.model_validate(user), tokens=_tokens_for(user))


@router.post("/login", response_model=AuthResponse)
@limiter.limit(AUTH_LIMIT)
async def login(
    request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)
) -> AuthResponse:
    user = await db.scalar(select(User).where(User.email == body.email))
    if user is None or not user.password_hash or not verify_password(
        body.password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    # Second factor, when enabled: accept a TOTP code or a one-time recovery code.
    if user.totp_enabled:
        if not body.code:
            raise HTTPException(status_code=401, detail="2fa_required")
        if not await _consume_second_factor(db, user, body.code):
            raise HTTPException(status_code=401, detail="2fa_invalid")

    # Login is an activity signal: refresh last_active_at and re-arm the
    # dead-man's-switch (clear any prior beneficiary notification).
    from datetime import datetime, timezone

    user.last_active_at = datetime.now(timezone.utc)
    if (user.settings or {}).get("beneficiary_notified"):
        s = dict(user.settings or {})
        s["beneficiary_notified"] = False
        user.settings = s
    await db.commit()

    return AuthResponse(user=UserOut.model_validate(user), tokens=_tokens_for(user))


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    try:
        payload = decode_token(body.refresh_token, expected_type="refresh")
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )
    user = await db.get(User, uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists"
        )
    if int(payload.get("tv", 0)) != int(user.token_version or 0):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked"
        )
    return _tokens_for(user)


@router.get("/me", response_model=UserOut)
async def me(current: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current)


# --- Email verification ---------------------------------------------------


@router.post("/verify-email/request", response_model=MessageResponse)
@limiter.limit(AUTH_LIMIT)
async def request_verification(
    request: Request, current: User = Depends(get_current_user)
) -> MessageResponse:
    """Re-send the verification email to the signed-in user."""
    if current.email_verified:
        return MessageResponse(message="Email уже подтверждён.")
    try:
        await send_verification_email(
            to=current.email, token=create_verification_token(str(current.id))
        )
    except Exception:  # noqa: BLE001
        pass
    return MessageResponse(message="Письмо с подтверждением отправлено.")


@router.post("/verify-email/confirm", response_model=MessageResponse)
async def confirm_verification(
    body: TokenOnlyRequest, db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    try:
        sub = decode_email_token(body.token, purpose="email_verify")
        user = await db.get(User, uuid.UUID(sub))
    except (JWTError, ValueError):
        raise HTTPException(status_code=400, detail="Недействительная или истёкшая ссылка.")
    if user is None:
        raise HTTPException(status_code=400, detail="Пользователь не найден.")
    was_unverified = not user.email_verified
    user.email_verified = True
    await db.commit()
    # First-time activation → welcome email (best-effort, never blocks).
    if was_unverified:
        try:
            await send_welcome_email(to=user.email, name=user.name)
        except Exception:  # noqa: BLE001
            pass
    return MessageResponse(message="Email подтверждён.")


# --- Password reset -------------------------------------------------------


@router.post("/password/forgot", response_model=MessageResponse)
@limiter.limit(FORGOT_LIMIT)
async def forgot_password(
    request: Request, body: EmailOnlyRequest, db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    """Always returns the same message — never reveals if the email exists."""
    user = await db.scalar(select(User).where(User.email == body.email))
    if user is not None and user.password_hash:
        try:
            await send_password_reset_email(
                to=user.email, token=create_password_reset_token(str(user.id))
            )
        except Exception:  # noqa: BLE001
            pass
    return MessageResponse(
        message="Если аккаунт существует, мы отправили письмо для сброса пароля."
    )


@router.post("/password/reset", response_model=MessageResponse)
@limiter.limit(AUTH_LIMIT)
async def reset_password(
    request: Request, body: PasswordResetRequest, db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    try:
        sub = decode_email_token(body.token, purpose="password_reset")
        user = await db.get(User, uuid.UUID(sub))
    except (JWTError, ValueError):
        raise HTTPException(status_code=400, detail="Недействительная или истёкшая ссылка.")
    if user is None:
        raise HTTPException(status_code=400, detail="Пользователь не найден.")
    user.password_hash = hash_password(body.password)
    await db.commit()
    return MessageResponse(message="Пароль обновлён.")


# --- Two-factor auth (TOTP) ---------------------------------------------------


async def _consume_second_factor(db: AsyncSession, user: User, code: str) -> bool:
    """Validate a TOTP code or burn a one-time recovery code. Commits on use."""
    code = (code or "").strip()
    secret = decrypt(user.totp_secret) if user.totp_secret else None
    if secret and verify_totp(secret, code):
        return True
    # Recovery code fallback (single-use).
    settings_dict = dict(user.settings or {})
    codes: list[str] = list(settings_dict.get("totp_recovery", []))
    hashed = hash_recovery_code(code)
    if hashed in codes:
        codes.remove(hashed)
        settings_dict["totp_recovery"] = codes
        user.settings = settings_dict
        await db.commit()
        return True
    return False


@router.get("/2fa/status", response_model=TwoFAStatus)
async def twofa_status(current: User = Depends(get_current_user)) -> TwoFAStatus:
    left = len((current.settings or {}).get("totp_recovery", []))
    return TwoFAStatus(enabled=bool(current.totp_enabled), recovery_codes_left=left)


@router.post("/2fa/setup", response_model=TwoFASetupResponse)
async def twofa_setup(
    current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> TwoFASetupResponse:
    """Generate a fresh secret (stored, not yet enabled) and an otpauth URI."""
    if current.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA уже включена.")
    secret = generate_secret()
    current.totp_secret = encrypt(secret)
    await db.commit()
    return TwoFASetupResponse(
        secret=secret, otpauth_uri=provisioning_uri(secret, current.email)
    )


@router.post("/2fa/enable", response_model=TwoFAEnableResponse)
async def twofa_enable(
    body: TwoFAEnableRequest,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TwoFAEnableResponse:
    """Confirm enrolment with a valid code; returns one-time recovery codes."""
    secret = decrypt(current.totp_secret) if current.totp_secret else None
    if not secret:
        raise HTTPException(status_code=400, detail="Сначала вызовите /2fa/setup.")
    if not verify_totp(secret, body.code):
        raise HTTPException(status_code=400, detail="Неверный код.")

    recovery = generate_recovery_codes()
    settings_dict = dict(current.settings or {})
    settings_dict["totp_recovery"] = [hash_recovery_code(c) for c in recovery]
    current.settings = settings_dict
    current.totp_enabled = True
    await db.commit()
    return TwoFAEnableResponse(recovery_codes=recovery)


@router.post("/2fa/disable", response_model=MessageResponse)
async def twofa_disable(
    body: CodeRequest,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    if not current.totp_enabled:
        return MessageResponse(message="2FA уже отключена.")
    if not await _consume_second_factor(db, current, body.code):
        raise HTTPException(status_code=400, detail="Неверный код.")
    current.totp_enabled = False
    current.totp_secret = None
    settings_dict = dict(current.settings or {})
    settings_dict.pop("totp_recovery", None)
    current.settings = settings_dict
    await db.commit()
    return MessageResponse(message="Двухфакторная аутентификация отключена.")


@router.post("/logout-all", response_model=TokenPair)
async def logout_all(
    current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> TokenPair:
    """Invalidate every existing token (all devices) and issue a fresh pair."""
    current.token_version = int(current.token_version or 0) + 1
    await db.commit()
    return _tokens_for(current)
