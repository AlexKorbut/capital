"""Validator service (Agent #6) — rules + preview. No LLM.

Checks the parsed ``AssetItem``s for sanity (positive amounts, plausible
currency codes, type-specific completeness) and decides whether the batch
should be flagged for human review. Mutates nothing destructively; it may
normalise obviously-fixable fields (uppercase currency/symbol) in place.
"""
from __future__ import annotations

from decimal import Decimal

from agents.state import AssetItem, ValidationResult

# A small allow-list of currencies we knowingly support FX for, plus common
# ones. Unknown codes don't fail validation (the world has many currencies) —
# they only raise a warning + review flag.
_KNOWN_FIAT = {
    "USD", "EUR", "GBP", "RUB", "BYN", "GEL", "KZT", "UAH", "PLN", "CHF",
    "JPY", "CNY", "TRY", "AED", "CAD", "AUD", "SEK", "NOK", "CZK", "AMD",
}
_KNOWN_CRYPTO = {"BTC", "ETH", "USDT", "USDC", "BNB", "SOL", "XRP", "TON", "TRX"}

_VALID_TYPES = {
    "cash", "bank_deposit", "crypto", "stock", "real_estate", "vehicle", "debt", "other",
}


def validate_assets(assets: list[AssetItem]) -> ValidationResult:
    """Validate a parsed batch, normalising in place. Returns a ValidationResult."""
    errors: list[str] = []
    warnings: list[str] = []
    needs_review = False

    if not assets:
        return ValidationResult(
            is_valid=False,
            needs_review=True,
            errors=[],
            warnings=["No assets were parsed from the input."],
        )

    for idx, a in enumerate(assets, start=1):
        label = a.location or a.note or a.currency or a.asset_type
        prefix = f"#{idx} ({label})"

        # --- asset_type ---
        if a.asset_type not in _VALID_TYPES:
            errors.append(f"{prefix}: unknown asset_type '{a.asset_type}'.")

        # --- currency / symbol normalisation ---
        if a.currency:
            a.currency = a.currency.strip().upper()
        if a.symbol:
            a.symbol = a.symbol.strip().upper()
        if a.ticker:
            a.ticker = a.ticker.strip().upper()

        # --- amount ---
        if a.amount is None:
            errors.append(f"{prefix}: missing amount.")
        elif a.amount <= Decimal(0):
            # Debts the user owes can legitimately be represented positive with
            # is_owed_to_me=False, so a non-positive amount is always suspect.
            errors.append(f"{prefix}: amount must be positive (got {a.amount}).")

        # --- type-specific checks ---
        if a.asset_type == "crypto":
            sym = a.symbol or a.currency
            if not sym:
                warnings.append(f"{prefix}: crypto without a symbol.")
                needs_review = True
            elif sym not in _KNOWN_CRYPTO:
                warnings.append(f"{prefix}: uncommon crypto symbol '{sym}'.")
                needs_review = True
        elif a.asset_type == "stock":
            if not a.ticker:
                warnings.append(f"{prefix}: stock without a ticker.")
                needs_review = True
        elif a.asset_type == "debt":
            if a.is_owed_to_me is None:
                warnings.append(f"{prefix}: debt direction (owed to/by) unclear.")
                needs_review = True
        else:
            # fiat-denominated assets
            if a.currency and a.currency not in _KNOWN_FIAT:
                warnings.append(f"{prefix}: uncommon currency '{a.currency}'.")
                needs_review = True

        # --- confidence ---
        if a.confidence < 0.6:
            warnings.append(
                f"{prefix}: low parser confidence ({a.confidence:.2f})."
            )
            needs_review = True

    is_valid = not errors
    if not is_valid:
        needs_review = True

    return ValidationResult(
        is_valid=is_valid,
        needs_review=needs_review,
        errors=errors,
        warnings=warnings,
    )
