"""Account router — GDPR data export + account deletion.

Export returns the user's full dataset (decrypted via the model layer) as JSON.
Delete removes the user and every related row; it is irreversible and requires
the caller to retype their email as confirmation.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from core.db import get_db
from core.deps import get_current_user
from models.advice import AdviceItem, AdviceSession
from models.asset import Asset
from models.billing import DebtRecord, SubscriptionEvent
from models.goal import Goal
from models.snapshot import Snapshot
from models.user import User
from models.wallet import Wallet
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
    wallets = list(await db.scalars(select(Wallet).where(Wallet.user_id == uid)))
    goals = list(await db.scalars(select(Goal).where(Goal.user_id == uid)))

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
        "wallets": [_row(w) for w in wallets],
        "goals": [_row(g) for g in goals],
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
    await db.execute(delete(AdviceItem).where(AdviceItem.user_id == uid))
    await db.execute(delete(AdviceSession).where(AdviceSession.user_id == uid))
    await db.execute(delete(Asset).where(Asset.user_id == uid))
    await db.execute(delete(Snapshot).where(Snapshot.user_id == uid))
    await db.execute(delete(Wallet).where(Wallet.user_id == uid))
    await db.execute(delete(Goal).where(Goal.user_id == uid))
    await db.execute(delete(SubscriptionEvent).where(SubscriptionEvent.user_id == uid))
    await db.execute(
        delete(DebtRecord).where(
            (DebtRecord.creditor_id == uid) | (DebtRecord.debtor_id == uid)
        )
    )
    await db.execute(delete(User).where(User.id == uid))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
