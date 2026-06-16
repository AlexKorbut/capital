"""Deterministic spreadsheet/CSV importer (Feature #3).

Maps a table (xlsx/xls/csv) straight into ``AssetItem``s by recognising column
headers (Russian or English synonyms) — no LLM involved, so it works identically
in demo and with real keys, and is more reliable than free-form parsing for
structured data. Unknown columns are ignored; missing optional fields are fine.

Expected (any subset, any order, RU or EN headers):
    тип | сумма | валюта | тикер | страна | место | ставка | количество
    type| amount| currency| ticker| country| location| rate | quantity
"""
from __future__ import annotations

import io
import logging
from decimal import Decimal, InvalidOperation

from agents.state import AssetItem, AssetType

logger = logging.getLogger("kapital.importer")

_EXCEL_EXT = (".xlsx", ".xls")

# header synonyms -> canonical field
_HEADER_MAP: dict[str, str] = {
    # type
    "тип": "asset_type", "тип актива": "asset_type", "категория": "asset_type",
    "type": "asset_type", "asset_type": "asset_type", "category": "asset_type",
    # amount
    "сумма": "amount", "количество": "amount", "кол-во": "amount", "баланс": "amount",
    "amount": "amount", "qty": "amount", "quantity": "amount", "balance": "amount",
    "value": "amount", "номинал": "amount",
    # currency
    "валюта": "currency", "currency": "currency", "ccy": "currency", "валюта/символ": "currency",
    # ticker / symbol
    "тикер": "ticker", "ticker": "ticker", "символ": "symbol", "symbol": "symbol",
    # country / location
    "страна": "country", "country": "country",
    "место": "location", "локация": "location", "банк": "location",
    "location": "location", "bank": "location", "account": "location", "счёт": "location",
    # rate
    "ставка": "interest_rate", "процент": "interest_rate", "rate": "interest_rate",
    "interest_rate": "interest_rate", "%": "interest_rate",
}

# value synonyms -> AssetType
_TYPE_VALUES: dict[str, AssetType] = {
    "наличные": "cash", "нал": "cash", "налик": "cash", "cash": "cash",
    "депозит": "bank_deposit", "вклад": "bank_deposit", "deposit": "bank_deposit",
    "bank_deposit": "bank_deposit", "счёт": "bank_deposit",
    "крипта": "crypto", "криптовалюта": "crypto", "crypto": "crypto",
    "акции": "stock", "акция": "stock", "stock": "stock", "etf": "stock", "фонд": "stock",
    "недвижимость": "real_estate", "недвижка": "real_estate", "real_estate": "real_estate",
    "квартира": "real_estate", "дом": "real_estate",
    "авто": "vehicle", "машина": "vehicle", "тачка": "vehicle", "car": "vehicle", "vehicle": "vehicle",
    "долг": "debt", "debt": "debt", "займ": "debt",
    "другое": "other", "other": "other",
}

_CRYPTO_SYMBOLS = {"BTC", "ETH", "USDT", "USDC", "BNB", "SOL", "XRP", "TON", "TRX"}


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    s = str(value).strip().replace(" ", "").replace(",", ".").replace("%", "")
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _norm_type(value) -> AssetType | None:
    if value is None:
        return None
    key = str(value).strip().lower()
    return _TYPE_VALUES.get(key)


def _read_rows(raw_bytes: bytes, filename: str) -> list[dict]:
    """Return list of {canonical_field: raw_value} dicts from the table."""
    import pandas as pd

    name = (filename or "").lower()
    if name.endswith(_EXCEL_EXT):
        frame = pd.read_excel(io.BytesIO(raw_bytes), dtype=str)
    else:
        for enc in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                frame = pd.read_csv(io.BytesIO(raw_bytes), dtype=str, encoding=enc)
                break
            except (UnicodeDecodeError, ValueError):
                continue
        else:
            return []

    frame = frame.fillna("")
    # Map headers -> canonical fields (unknown headers dropped).
    colmap: dict[str, str] = {}
    for col in frame.columns:
        canonical = _HEADER_MAP.get(str(col).strip().lower())
        if canonical:
            colmap[col] = canonical

    rows: list[dict] = []
    for _, r in frame.iterrows():
        row = {canonical: r[col] for col, canonical in colmap.items()}
        if any(str(v).strip() for v in row.values()):
            rows.append(row)
    return rows


def parse_table_bytes(raw_bytes: bytes, filename: str = "") -> list[AssetItem]:
    """Parse a spreadsheet/CSV into AssetItems. Returns [] if nothing usable."""
    if not raw_bytes:
        return []
    try:
        rows = _read_rows(raw_bytes, filename)
    except Exception as e:  # noqa: BLE001 — bad file shouldn't 500
        logger.warning("import: failed to read table: %s", e)
        return []

    items: list[AssetItem] = []
    for row in rows:
        amount = _dec(row.get("amount"))
        if amount is None or amount == 0:
            continue

        currency = (str(row.get("currency") or "").strip().upper()) or None
        ticker = (str(row.get("ticker") or "").strip().upper()) or None
        symbol = (str(row.get("symbol") or "").strip().upper()) or None

        asset_type = _norm_type(row.get("asset_type"))
        if asset_type is None:
            # Infer: crypto symbol -> crypto, ticker present -> stock, else cash.
            sym = symbol or currency
            if ticker:
                asset_type = "stock"
            elif sym and sym in _CRYPTO_SYMBOLS:
                asset_type = "crypto"
            else:
                asset_type = "cash"

        if asset_type == "crypto":
            sym = symbol or currency
            items.append(AssetItem(
                asset_type="crypto", amount=amount, currency=(sym or "BTC"),
                symbol=sym, quantity=amount, confidence=1.0,
            ))
        elif asset_type == "stock":
            tk = ticker or symbol
            items.append(AssetItem(
                asset_type="stock", amount=amount, currency=(currency or "USD"),
                ticker=tk, symbol=tk, quantity=amount, confidence=1.0,
            ))
        else:
            items.append(AssetItem(
                asset_type=asset_type, amount=amount, currency=(currency or "USD"),
                country=(str(row.get("country") or "").strip().upper() or None),
                location=(str(row.get("location") or "").strip() or None),
                interest_rate=_dec(row.get("interest_rate")),
                confidence=1.0,
            ))
    return items


# A ready-to-edit template the UI can offer for download.
CSV_TEMPLATE = (
    "тип,сумма,валюта,тикер,страна,место,ставка\n"
    "наличные,5000,USD,,,кошелёк,\n"
    "депозит,10000,EUR,,DE,N26,3.5\n"
    "крипта,0.5,BTC,,,,\n"
    "акции,10,USD,AAPL,US,IBKR,\n"
    "недвижимость,80000,USD,,GE,Батуми,\n"
)
