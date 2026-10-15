"""Finance project schemas (fin-01).

Requests are strict (unknown fields -> 422: a typo must not be silently dropped)
and validated at the boundary. Responses carry money as strings (never floats).
"""
from __future__ import annotations

import unicodedata
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Any, Literal, Optional

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    StringConstraints,
    model_validator,
)

NAME_MAX = 120
CATEGORY_MAX = 60
NOTE_MAX = 2000
MAX_AMOUNT = Decimal(10) ** 12
AMOUNT_PLACES = 6                      # fin_expenses.amount is Numeric(20, 6)
FX_RATE_MAX = Decimal(10) ** 9         # fx_rate is Numeric(20, 10)
FX_RATE_MIN = Decimal("0.0000000001")  # smallest storable positive rate
EARLIEST_DAY = date(1970, 1, 1)
RAW_TEXT_MAX = 20_000  # a hard ceiling on what is even normalised; the real limits come after

# Active ISO-4217 currency codes (no precious metals, funds or test codes).
ISO_4217 = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN
    BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD
    FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY
    KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP
    MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR
    RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT
    TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XCG XOF XPF YER ZAR
    ZMW ZWG
    """.split()
)


# --- reusable field types -----------------------------------------------------------


def normalize_currency(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 3 or not code.isascii() or code not in ISO_4217:
        raise ValueError("currency must be a 3-letter ISO-4217 code")
    return code


def check_spent_on(value: date) -> date:
    # "Tomorrow" is allowed: the caller's local date can be ahead of UTC.
    latest = datetime.now(timezone.utc).date() + timedelta(days=1)
    if value < EARLIEST_DAY or value > latest:
        raise ValueError(f"date must be between {EARLIEST_DAY.isoformat()} and tomorrow")
    return value


def _no_bool(value: Any) -> Any:
    if isinstance(value, bool):
        raise ValueError("a number is required")
    return value


def _check_amount(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("amount must be a finite number")
    if value == 0:
        raise ValueError("amount must not be zero")
    if abs(value) > MAX_AMOUNT:
        raise ValueError("amount must not exceed 10^12 in absolute value")
    if value.normalize().as_tuple().exponent < -AMOUNT_PLACES:
        raise ValueError(f"amount allows at most {AMOUNT_PLACES} decimal places")
    return value


def _check_fx_rate(value: Decimal) -> Decimal:
    if not value.is_finite() or value <= 0 or value >= FX_RATE_MAX:
        raise ValueError("fx_rate must be a positive number below 10^9")
    if value.quantize(FX_RATE_MIN) <= 0:
        raise ValueError("fx_rate is too small")
    return value


def _no_nul(value: str) -> None:
    # Postgres refuses NUL in text columns (-> 500); reject it at the door.
    if "\x00" in value:
        raise ValueError("text must not contain NUL characters")


def _tidy(value: str) -> str:
    """NFC, then whitespace runs (tabs, newlines, NBSP ...) collapsed to single spaces."""
    return " ".join(unicodedata.normalize("NFC", value).split())


def _limit(text: str, maximum: int, what: str) -> str:
    # Measured AFTER normalisation: that is what the column will hold.
    if len(text) > maximum:
        raise ValueError(f"{what} must be at most {maximum} characters")
    return text


def normalize_name(value: str) -> str:
    _no_nul(value)
    text = _tidy(value)
    if not text:
        raise ValueError("name must not be blank")
    return _limit(text, NAME_MAX, "name")


def normalize_category(value: Optional[str]) -> Optional[str]:
    """Trim, NFC, collapse whitespace, lowercase: 'Топливо ' and 'топливо' are one category."""
    if value is None:
        return None
    _no_nul(value)
    # lower() can lengthen text ("İ" -> "i" + combining dot), hence NFC again and measure last.
    text = unicodedata.normalize("NFC", _tidy(value).lower())
    return _limit(text, CATEGORY_MAX, "category") if text else None


def normalize_note(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    _no_nul(value)
    text = unicodedata.normalize("NFC", value).strip()
    return _limit(text, NOTE_MAX, "note") if text else None


CurrencyCode = Annotated[str, AfterValidator(normalize_currency)]
SpentOn = Annotated[date, AfterValidator(check_spent_on)]
Amount = Annotated[Decimal, BeforeValidator(_no_bool), AfterValidator(_check_amount)]
FxRate = Annotated[Decimal, BeforeValidator(_no_bool), AfterValidator(_check_fx_rate)]
_Raw = Annotated[str, StringConstraints(max_length=RAW_TEXT_MAX)]
ProjectName = Annotated[_Raw, AfterValidator(normalize_name)]
Category = Annotated[Optional[_Raw], AfterValidator(normalize_category)]
Note = Annotated[Optional[_Raw], AfterValidator(normalize_note)]
ProjectStatus = Literal["active", "archived"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _reject_explicit_null(model: BaseModel, fields: tuple[str, ...]) -> None:
    for field in fields:
        if field in model.model_fields_set and getattr(model, field) is None:
            raise ValueError(f"{field} cannot be null")


# --- projects --------------------------------------------------------------------------


class ProjectCreate(_Strict):
    name: ProjectName
    base_currency: Optional[CurrencyCode] = None  # default: the user's base currency
    started_on: Optional[date] = None
    note: Note = None


class ProjectUpdate(_Strict):
    name: Optional[ProjectName] = None
    base_currency: Optional[CurrencyCode] = None
    status: Optional[ProjectStatus] = None
    started_on: Optional[date] = None
    note: Note = None

    @model_validator(mode="after")
    def _required_when_present(self) -> "ProjectUpdate":
        _reject_explicit_null(self, ("name", "base_currency", "status"))
        return self


class ProjectOut(BaseModel):
    id: str
    name: str
    note: Optional[str] = None
    base_currency: str
    status: str
    started_on: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    total_base: str
    expenses_count: int
    last_spent_on: Optional[str] = None
    unpriced_count: int


# --- expenses ----------------------------------------------------------------------------


class ExpenseCreate(_Strict):
    amount: Amount
    currency: CurrencyCode
    spent_on: SpentOn
    category: Category = None
    note: Note = None
    fx_rate: Optional[FxRate] = None  # your own rate: base units per 1 unit of currency
    client_id: Optional[uuid.UUID] = None


class ExpenseUpdate(_Strict):
    amount: Optional[Amount] = None
    currency: Optional[CurrencyCode] = None
    spent_on: Optional[SpentOn] = None
    category: Category = None
    note: Note = None
    # A number sets your own rate; an explicit null returns to the market rate.
    fx_rate: Optional[FxRate] = None

    @model_validator(mode="after")
    def _required_when_present(self) -> "ExpenseUpdate":
        _reject_explicit_null(self, ("amount", "currency", "spent_on"))
        return self


class AttachmentBrief(BaseModel):
    """A receipt photo as listed inside an expense. No name, no hash, no storage key."""

    id: str
    mime: str
    size_bytes: int
    created_at: Optional[str] = None


class AttachmentOut(AttachmentBrief):
    """The answer to an upload: the brief plus the name the client gave the file (display only)."""

    original_name: Optional[str] = None


class ExpenseOut(BaseModel):
    id: str
    project_id: str
    amount: str
    currency: str
    spent_on: str
    category: Optional[str] = None
    note: Optional[str] = None
    base_amount: Optional[str] = None
    base_currency: str
    fx_rate: Optional[str] = None
    fx_date: Optional[str] = None
    fx_source: Optional[str] = None
    client_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    attachments: list[AttachmentBrief] = []


class ExpenseList(BaseModel):
    items: list[ExpenseOut]
    total: int
    limit: int
    offset: int


# --- report / revalue / fx / categories --------------------------------------------------


class ReportProject(BaseModel):
    id: str
    name: str
    base_currency: str
    status: str
    started_on: Optional[str] = None


class UnpricedCurrency(BaseModel):
    currency: str
    count: int
    amount: str


class CategoryTotal(BaseModel):
    category: Optional[str] = None
    total_base: str
    count: int
    unpriced_count: int


class MonthTotal(BaseModel):
    month: str
    total_base: str
    count: int
    unpriced_count: int


class CurrencyTotal(BaseModel):
    currency: str
    amount: str
    total_base: str
    count: int
    unpriced_count: int


class ProjectReport(BaseModel):
    project: ReportProject
    period: dict[str, Optional[str]]
    base_currency: str
    total_base: str
    expenses_count: int
    unpriced_count: int
    unpriced_by_currency: list[UnpricedCurrency]
    by_category: list[CategoryTotal]
    by_month: list[MonthTotal]
    by_currency: list[CurrencyTotal]


class RevalueResult(BaseModel):
    revalued: int
    unpriced_count: int


class FxPreview(BaseModel):
    currency: str
    base: str
    date: str
    rate: Optional[str] = None
    fx_date: Optional[str] = None
    source: Optional[str] = None


class CategoryHint(BaseModel):
    name: str
    count: int


# --- formatting ----------------------------------------------------------------------------


def dec_str(value: Optional[Decimal], min_places: int = 2) -> Optional[str]:
    """Decimal -> JSON string without trailing zeros beyond ``min_places`` ("50.00", "1.1")."""
    if value is None:
        return None
    if value == 0:
        value = Decimal(0)  # never "-0.00"
    text = format(value, "f")
    whole, _, frac = text.partition(".")
    frac = frac.rstrip("0").ljust(min_places, "0")
    return f"{whole}.{frac}" if frac else whole
