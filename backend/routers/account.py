"""Account router — GDPR data export + account deletion.

Export returns the user's full dataset (decrypted via the model layer) as JSON.
Delete removes the user and every related row; it is irreversible and requires
the caller to retype their email as confirmation.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel, Field

from core.config import settings
from core.crypto import encrypt
from core.db import get_db
from core.deps import get_current_user
from core.llm import (
    PROVIDERS,
    ModelRole,
    is_demo_for,
    mask_key,
    set_user_ai,
    user_ai_from_settings,
)
from core.tokens import generate_token, hash_token, visible_prefix
from models.advice import AdviceItem, AdviceSession
from models.api_token import ApiToken
from models.asset import Asset
from models.billing import DebtRecord, SubscriptionEvent
from models.snapshot import Snapshot
from models.user import User
from schemas.account import DeleteAccountRequest

router = APIRouter(prefix="/account", tags=["account"])


class NotificationPrefs(BaseModel):
    emails_enabled: bool


class LanguageUpdate(BaseModel):
    lang: str


@router.put("/language", response_model=LanguageUpdate)
async def set_language(
    body: LanguageUpdate,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LanguageUpdate:
    """Persist the UI language so advisor output and emails follow it."""
    lang = "en" if body.lang.lower().startswith("en") else "ru"
    s = dict(current.settings or {})
    s["lang"] = lang
    current.settings = s
    await db.commit()
    return LanguageUpdate(lang=lang)


class BaseCurrencyUpdate(BaseModel):
    base_currency: str


@router.put("/base-currency", response_model=BaseCurrencyUpdate)
async def set_base_currency(
    body: BaseCurrencyUpdate,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BaseCurrencyUpdate:
    """Change the display/base currency (totals are normalised to it)."""
    code = (body.base_currency or "USD").strip().upper()[:3]
    current.base_currency = code or "USD"
    await db.commit()
    return BaseCurrencyUpdate(base_currency=current.base_currency)


class BeneficiaryConfig(BaseModel):
    email: str | None = None
    days: int = 0


@router.get("/beneficiary", response_model=BeneficiaryConfig)
async def get_beneficiary(current: User = Depends(get_current_user)) -> BeneficiaryConfig:
    s = current.settings or {}
    return BeneficiaryConfig(
        email=s.get("beneficiary_email"), days=int(s.get("beneficiary_days") or 0)
    )


@router.put("/beneficiary", response_model=BeneficiaryConfig)
async def set_beneficiary(
    body: BeneficiaryConfig,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BeneficiaryConfig:
    """Set or clear the trusted contact + inactivity threshold (dead-man's switch)."""
    s = dict(current.settings or {})
    email = (body.email or "").strip()
    if email and body.days > 0:
        s["beneficiary_email"] = email
        s["beneficiary_days"] = int(body.days)
        s["beneficiary_notified"] = False
    else:
        s.pop("beneficiary_email", None)
        s.pop("beneficiary_days", None)
        s.pop("beneficiary_notified", None)
    current.settings = s
    await db.commit()
    return BeneficiaryConfig(
        email=s.get("beneficiary_email"), days=int(s.get("beneficiary_days") or 0)
    )


@router.get("/notifications", response_model=NotificationPrefs)
async def get_notifications(current: User = Depends(get_current_user)) -> NotificationPrefs:
    return NotificationPrefs(emails_enabled=not (current.settings or {}).get("no_emails"))


@router.put("/notifications", response_model=NotificationPrefs)
async def set_notifications(
    body: NotificationPrefs,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationPrefs:
    settings_dict = dict(current.settings or {})
    settings_dict["no_emails"] = not body.emails_enabled
    current.settings = settings_dict
    await db.commit()
    return NotificationPrefs(emails_enabled=body.emails_enabled)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    return value


def _row(obj: Any) -> dict[str, Any]:
    return {
        c.name: _jsonable(getattr(obj, c.name))
        for c in obj.__table__.columns
    }


@router.get("/export")
async def export_account(
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Full machine-readable dump of the user's data (GDPR portability)."""
    uid = current.id

    snapshots = list(
        await db.scalars(select(Snapshot).where(Snapshot.user_id == uid))
    )
    assets = list(await db.scalars(select(Asset).where(Asset.user_id == uid)))
    sessions = list(
        await db.scalars(select(AdviceSession).where(AdviceSession.user_id == uid))
    )
    items = list(await db.scalars(select(AdviceItem).where(AdviceItem.user_id == uid)))
    sub_events = list(
        await db.scalars(
            select(SubscriptionEvent).where(SubscriptionEvent.user_id == uid)
        )
    )
    debts = list(
        await db.scalars(
            select(DebtRecord).where(
                (DebtRecord.creditor_id == uid) | (DebtRecord.debtor_id == uid)
            )
        )
    )

    user_row = _row(current)
    user_row.pop("password_hash", None)  # never export the secret

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user": user_row,
        "snapshots": [_row(s) for s in snapshots],
        "assets": [_row(a) for a in assets],
        "advice_sessions": [_row(s) for s in sessions],
        "advice_items": [_row(i) for i in items],
        "subscription_events": [_row(e) for e in sub_events],
        "debt_records": [_row(d) for d in debts],
    }


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_account(
    body: DeleteAccountRequest,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Irreversibly delete the account and all related data.

    Confirmation: the caller must retype their exact email.
    """
    if body.confirm_email.strip().lower() != current.email.lower():
        raise HTTPException(
            status_code=400,
            detail="Подтверждение не совпадает с email аккаунта.",
        )

    uid = current.id
    # Delete children first (explicit — don't rely on SQLite FK cascade being on).
    await db.execute(delete(ApiToken).where(ApiToken.user_id == uid))
    await db.execute(delete(AdviceItem).where(AdviceItem.user_id == uid))
    await db.execute(delete(AdviceSession).where(AdviceSession.user_id == uid))
    await db.execute(delete(Asset).where(Asset.user_id == uid))
    await db.execute(delete(Snapshot).where(Snapshot.user_id == uid))
    await db.execute(delete(SubscriptionEvent).where(SubscriptionEvent.user_id == uid))
    await db.execute(
        delete(DebtRecord).where(
            (DebtRecord.creditor_id == uid) | (DebtRecord.debtor_id == uid)
        )
    )
    await db.execute(delete(User).where(User.id == uid))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Personal API tokens (machine clients such as Jarvis) ----------------------


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiTokenOut(BaseModel):
    id: UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None


class ApiTokenCreated(ApiTokenOut):
    # The plaintext token — returned exactly once at creation, never stored.
    token: str


def _token_out(row: ApiToken) -> ApiTokenOut:
    return ApiTokenOut(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


@router.get("/tokens", response_model=list[ApiTokenOut])
async def list_tokens(
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiTokenOut]:
    """Active personal API tokens of the caller (revoked ones are not listed)."""
    rows = await db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == current.id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at)
    )
    return [_token_out(r) for r in rows.all()]


@router.post("/tokens", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    body: ApiTokenCreate,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiTokenCreated:
    """Mint a personal API token. The plaintext is shown once — copy it now."""
    plain = generate_token()
    row = ApiToken(
        user_id=current.id,
        name=body.name.strip(),
        token_hash=hash_token(plain),
        prefix=visible_prefix(plain),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return ApiTokenCreated(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        created_at=row.created_at,
        last_used_at=None,
        token=plain,
    )


@router.delete(
    "/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def revoke_token(
    token_id: UUID,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Revoke a token. Unknown, foreign or already-revoked tokens answer 404."""
    row = await db.scalar(
        select(ApiToken).where(
            ApiToken.id == token_id,
            ApiToken.user_id == current.id,
            ApiToken.revoked_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    row.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- AI: bring your own keys or use KAPITAL's ---------------------------------


class AiProviderOut(BaseModel):
    provider: str
    label: str
    configured: bool
    hint: str | None = None


class AiSettingsOut(BaseModel):
    mode: Literal["kapital", "own"]
    providers: list[AiProviderOut]
    # This server has its own provider keys (the "KAPITAL AI" option works).
    server_ai_available: bool
    # Would an LLM-backed request from this user run as a deterministic stub now?
    demo: bool


class AiSettingsUpdate(BaseModel):
    mode: Literal["kapital", "own"] | None = None
    # provider -> plaintext key; null or "" removes the stored key.
    keys: dict[str, str | None] | None = None


_MAX_KEY_LEN = 4096


def _ai_out(current: User) -> AiSettingsOut:
    ai = user_ai_from_settings(current.settings)
    set_user_ai(ai)  # make `demo` reflect the (possibly just-saved) config
    mode: Literal["kapital", "own"] = "own" if ai.mode == "own" else "kapital"
    return AiSettingsOut(
        mode=mode,
        providers=[
            AiProviderOut(
                provider=p,
                label=label,
                configured=p in ai.keys,
                hint=mask_key(ai.keys[p]) if p in ai.keys else None,
            )
            for p, label in PROVIDERS.items()
        ],
        server_ai_available=settings.llm_keys_present,
        demo=is_demo_for(ModelRole.PARSER),
    )


@router.get("/ai", response_model=AiSettingsOut)
async def get_ai_settings(current: User = Depends(get_current_user)) -> AiSettingsOut:
    """AI mode and which provider keys are on file (masked; never the key itself)."""
    return _ai_out(current)


@router.put("/ai", response_model=AiSettingsOut)
async def set_ai_settings(
    body: AiSettingsUpdate,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AiSettingsOut:
    """Switch between KAPITAL AI and your own keys; store keys encrypted at rest."""
    s = dict(current.settings or {})
    ai_cfg = dict(s.get("ai") or {})
    stored = dict(ai_cfg.get("keys") or {})
    if body.mode is not None:
        ai_cfg["mode"] = body.mode
    for provider, key in (body.keys or {}).items():
        if provider not in PROVIDERS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown provider '{provider}'. Known: {', '.join(PROVIDERS)}",
            )
        value = (key or "").strip()
        if not value:
            stored.pop(provider, None)
            continue
        if len(value) > _MAX_KEY_LEN:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Key for '{provider}' is too long",
            )
        stored[provider] = encrypt(value)
    ai_cfg["keys"] = stored
    s["ai"] = ai_cfg
    current.settings = s
    await db.commit()
    return _ai_out(current)
