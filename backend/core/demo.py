"""Demo-mode stubs for the LLM-backed services.

When ``settings.is_demo`` is true (no provider keys configured in dev, or
``DEMO_MODE=true``), the LLM services route here instead of calling a model, so
the entire product is testable end-to-end with NO API keys.

The stubs are deliberately *useful*, not random:
  - ``demo_parse`` is a small deterministic heuristic that understands the most
    common Russian/English phrasings (``10к евро``, ``2 битка``, ``депозит 1000
    лари под 9%``); anything it can't parse falls back to a canned sample
    portfolio so the preview screen is never empty.
  - ``demo_advice`` / ``demo_geo_observations`` are *computed from the real
    portfolio* (concentration, currency mix, liquidity), so the Advisor and Geo
    screens show numbers that actually match the user's data.

Nothing here ever touches the network or a provider SDK.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from agents.state import AssetItem, ParsedPortfolio

# --- Parser heuristic ---------------------------------------------------------

# currency keyword -> ISO code
_CCY_WORDS: dict[str, str] = {
    "евро": "EUR", "eur": "EUR", "€": "EUR",
    "доллар": "USD", "долларов": "USD", "бакс": "USD", "баксов": "USD", "usd": "USD", "$": "USD",
    "рубл": "RUB", "rub": "RUB", "₽": "RUB",
    "лари": "GEL", "gel": "GEL", "₾": "GEL",
    "зайчик": "BYN", "byn": "BYN", "бел": "BYN",
    "тенге": "KZT", "kzt": "KZT",
    "злот": "PLN", "pln": "PLN",
    "гривен": "UAH", "грн": "UAH", "uah": "UAH",
}

# crypto keyword -> symbol
_CRYPTO_WORDS: dict[str, str] = {
    "битк": "BTC", "биток": "BTC", "битка": "BTC", "биток": "BTC", "btc": "BTC", "бтс": "BTC",
    "эфир": "ETH", "эфира": "ETH", "eth": "ETH",
    "usdt": "USDT", "тезер": "USDT", "тизер": "USDT",
    "solana": "SOL", "сол": "SOL", "sol": "SOL",
    "bnb": "BNB",
}

# company name -> ticker (demo heuristic for the stock branch)
_TICKER_WORDS: dict[str, str] = {
    "apple": "AAPL", "эпл": "AAPL", "эппл": "AAPL",
    "tesla": "TSLA", "тесла": "TSLA",
    "microsoft": "MSFT", "майкрософт": "MSFT",
    "google": "GOOGL", "гугл": "GOOGL",
    "amazon": "AMZN", "амазон": "AMZN",
    "nvidia": "NVDA", "нвидиа": "NVDA",
    "meta": "META", "facebook": "META",
    "netflix": "NFLX", "нетфликс": "NFLX",
    "sp500": "SPY", "s&p": "SPY", "спай": "SPY",
}

# country hints
_COUNTRY_WORDS: dict[str, tuple[str, str]] = {
    "минск": ("BY", "Minsk"),
    "minsk": ("BY", "Minsk"),
    "бога": ("GE", "Batumi"),
    "боге": ("GE", "Batumi"),
    "батуми": ("GE", "Batumi"),
    "batumi": ("GE", "Batumi"),
    "тбилиси": ("GE", "Tbilisi"),
    "tbilisi": ("GE", "Tbilisi"),
    "москв": ("RU", "Moscow"),
    "moscow": ("RU", "Moscow"),
    "варшав": ("PL", "Warsaw"),
    "warsaw": ("PL", "Warsaw"),
}


def _to_decimal(num: str) -> Decimal:
    """Parse a human-typed number, tolerating thousands separators.

    Handles '1.000.000' / '1 000 000' / '1,000,000.50' style grouping and never
    raises — an unparseable amount yields Decimal(0) so the demo parser can skip
    it instead of crashing on arbitrary user text.
    """
    raw = (num or "").strip()
    if not raw:
        return Decimal(0)

    # Spaces are always thousands separators here.
    raw = raw.replace(" ", "")
    has_dot = "." in raw
    has_comma = "," in raw

    if has_dot and has_comma:
        # The right-most separator is the decimal point; the other groups.
        if raw.rfind(",") > raw.rfind("."):
            normalized = raw.replace(".", "").replace(",", ".")
        else:
            normalized = raw.replace(",", "")
    elif has_comma:
        # Comma alone: decimal if a single comma with <=2 trailing digits,
        # otherwise thousands grouping.
        if raw.count(",") == 1 and len(raw.split(",")[1]) <= 2:
            normalized = raw.replace(",", ".")
        else:
            normalized = raw.replace(",", "")
    elif has_dot:
        # Dot alone: thousands grouping if repeated or grouped in 3s
        # (e.g. '1.000.000'), otherwise a decimal point.
        if raw.count(".") > 1:
            normalized = raw.replace(".", "")
        else:
            integer, _, frac = raw.partition(".")
            if len(frac) == 3 and len(integer) <= 3:
                normalized = raw.replace(".", "")  # '1.000' -> 1000
            else:
                normalized = raw
    else:
        normalized = raw

    try:
        return Decimal(normalized)
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _expand_amount(num: str, suffix: str) -> Decimal:
    """'10' + 'к' -> 100000... actually 10*1000; '2' + 'кк' -> 2_000_000."""
    value = _to_decimal(num)
    s = (suffix or "").lower()
    if s in ("к", "k", "тыс", "тысяч"):
        value *= Decimal(1000)
    elif s in ("кк", "млн", "м", "m", "ляма", "лям", "лимон"):
        value *= Decimal(1_000_000)
    return value


_SEGMENT_RE = re.compile(r"[,;\n]|(?:\bи\b)", re.IGNORECASE)
_NUM_RE = re.compile(r"(\d[\d\s.,]*)\s*(кк|к|k|млн|млрд|m|тыс|тысяч|ляма|лям|лимон)?", re.IGNORECASE)
_RATE_RE = re.compile(r"под\s*(\d+(?:[.,]\d+)?)\s*%|(\d+(?:[.,]\d+)?)\s*%")


def _parse_segment(seg: str) -> AssetItem | None:
    low = seg.lower()
    num_match = _NUM_RE.search(seg)
    if not num_match:
        return None
    amount = _expand_amount(num_match.group(1), num_match.group(2))
    if amount == 0:
        return None

    # crypto?
    for kw, sym in _CRYPTO_WORDS.items():
        if kw in low:
            return AssetItem(
                asset_type="crypto", amount=amount, currency=sym, symbol=sym,
                quantity=amount, confidence=0.9, note=seg.strip(),
            )

    # currency
    currency = "USD"
    for kw, code in _CCY_WORDS.items():
        if kw in low:
            currency = code
            break

    # country / location
    country = location = None
    for kw, (cc, loc) in _COUNTRY_WORDS.items():
        if kw in low:
            country, location = cc, loc
            break

    # asset type
    asset_type = "cash"
    interest_rate = None
    if any(w in low for w in ("депозит", "вклад", "deposit")):
        asset_type = "bank_deposit"
        rate_m = _RATE_RE.search(seg)
        if rate_m:
            try:
                interest_rate = Decimal((rate_m.group(1) or rate_m.group(2)).replace(",", "."))
            except (InvalidOperation, ValueError):
                interest_rate = None
    elif any(w in low for w in ("квартир", "дом", "недвиж", "apartment", "house")):
        asset_type = "real_estate"
    elif any(w in low for w in ("машин", "авто", "тачк", "car", "vehicle")):
        asset_type = "vehicle"
    elif any(w in low for w in ("акци", "stock", "shares", "etf", "фонд")):
        asset_type = "stock"
        ticker = None
        for kw, tk in _TICKER_WORDS.items():
            if kw in low:
                ticker = tk
                break
        if ticker is None:
            m = re.search(r"\b([A-Z]{2,5})\b", seg)  # bare uppercase ticker
            if m:
                ticker = m.group(1)
        return AssetItem(
            asset_type="stock", amount=amount, currency="USD",
            ticker=ticker, symbol=ticker, quantity=amount,
            confidence=0.8 if ticker else 0.55, note=seg.strip(),
        )
    elif any(w in low for w in ("должен мне", "должны мне", "в долг", "одолжил")):
        asset_type = "debt"

    return AssetItem(
        asset_type=asset_type, amount=amount, currency=currency,
        country=country, location=location, interest_rate=interest_rate,
        confidence=0.85, note=seg.strip(),
    )


def _canned_portfolio() -> list[AssetItem]:
    """Realistic sample shown when the heuristic finds nothing."""
    return [
        AssetItem(asset_type="cash", amount=Decimal(10000), currency="EUR",
                  country="BY", location="Minsk", confidence=0.8, note="нал евро"),
        AssetItem(asset_type="crypto", amount=Decimal(2), currency="BTC", symbol="BTC",
                  quantity=Decimal(2), confidence=0.9, note="2 BTC"),
        AssetItem(asset_type="bank_deposit", amount=Decimal(1000), currency="GEL",
                  country="GE", location="Bank of Georgia", interest_rate=Decimal(9),
                  confidence=0.85, note="депозит под 9%"),
    ]


def demo_parse(text: str, base_currency: str = "USD") -> ParsedPortfolio:
    text = (text or "").strip()
    if not text:
        return ParsedPortfolio(assets=[], needs_review=True,
                               parser_notes="Пустой ввод — нечего разбирать.")

    assets: list[AssetItem] = []
    for seg in _SEGMENT_RE.split(text):
        if not seg or not seg.strip():
            continue
        item = _parse_segment(seg)
        if item is not None:
            assets.append(item)

    if not assets:
        return ParsedPortfolio(
            assets=_canned_portfolio(), needs_review=True,
            parser_notes="Демо-режим: показан пример портфеля (LLM-ключ не задан).",
        )

    needs_review = any(a.confidence < 0.7 for a in assets)
    return ParsedPortfolio(
        assets=assets, needs_review=needs_review,
        parser_notes="Демо-режим: эвристический разбор без LLM.",
    )


# --- Advisor (computed from the real portfolio) -------------------------------

_LIQUID = {"cash", "crypto"}


def _priced(assets: list[AssetItem]) -> list[AssetItem]:
    return [a for a in assets if a.usd_value is not None]


def demo_advice(
    assets: list[AssetItem],
    total_usd: Decimal | None = None,
    geo: dict[str, Any] | None = None,
    news: list[dict[str, Any]] | None = None,
    language: str = "ru",
) -> list[dict[str, Any]]:
    en = language == "en"

    def L(ru: str, eng: str) -> str:
        return eng if en else ru

    priced = _priced(assets)
    if not priced:
        return [{
            "title": L("Недостаточно данных для анализа", "Not enough data to analyze"),
            "category": "general",
            "body": L(
                "У активов пока нет оценки в USD, поэтому структуру капитала "
                "оценить нельзя. Добавьте суммы и валюты — и аналитика появится.",
                "Your assets aren't valued in USD yet, so the capital structure can't "
                "be assessed. Add amounts and currencies — analytics will appear.",
            ),
            "relevance": None,
        }]

    total = total_usd or sum((a.usd_value for a in priced), Decimal(0))
    insights: list[dict[str, Any]] = []

    # 1. Concentration in a single asset
    top = max(priced, key=lambda a: abs(a.usd_value))
    if total and total != 0:
        share = (abs(top.usd_value) / abs(total) * Decimal(100)).quantize(Decimal("0.1"))
        if share >= Decimal("40"):
            sym = top.symbol or top.currency
            insights.append({
                "title": L(
                    f"Высокая концентрация: {share}% в одном активе",
                    f"High concentration: {share}% in one asset",
                ),
                "category": "concentration",
                "body": L(
                    f"На один актив ({top.asset_type}, {sym}) приходится {share}% "
                    f"капитала. Такая структура повышает чувствительность всего "
                    f"портфеля к колебаниям этого актива.",
                    f"A single asset ({top.asset_type}, {sym}) makes up {share}% of "
                    f"your capital. This raises the whole portfolio's sensitivity to "
                    f"that asset's swings.",
                ),
                "relevance": sym,
            })

    # 2. Currency mix
    by_ccy: dict[str, Decimal] = {}
    for a in priced:
        key = a.symbol or a.currency or "—"
        by_ccy[key] = by_ccy.get(key, Decimal(0)) + abs(a.usd_value)
    if total and total != 0 and by_ccy:
        top_ccy, top_val = max(by_ccy.items(), key=lambda kv: kv[1])
        ccy_share = (top_val / abs(total) * Decimal(100)).quantize(Decimal("0.1"))
        insights.append({
            "title": L(
                f"Валютная структура: {len(by_ccy)} валют(ы)",
                f"Currency mix: {len(by_ccy)} currenc(ies)",
            ),
            "category": "currency",
            "body": L(
                f"Крупнейшая позиция по валюте — {top_ccy} ({ccy_share}% капитала). "
                f"Чем больше доля одной валюты, тем сильнее капитал зависит от её курса.",
                f"The largest currency position is {top_ccy} ({ccy_share}% of capital). "
                f"The bigger one currency's share, the more your wealth depends on its rate.",
            ),
            "relevance": top_ccy,
        })

    # 3. Liquidity
    liquid = sum((abs(a.usd_value) for a in priced if a.asset_type in _LIQUID), Decimal(0))
    if total and total != 0:
        lshare = (liquid / abs(total) * Decimal(100)).quantize(Decimal("0.1"))
        insights.append({
            "title": L(f"Ликвидная часть: {lshare}%", f"Liquid share: {lshare}%"),
            "category": "liquidity",
            "body": L(
                f"Около {lshare}% капитала — в наличных и крипте (быстро доступно). "
                f"Остальное в менее ликвидных формах (депозиты, недвижимость, долги).",
                f"About {lshare}% of capital is in cash and crypto (quickly accessible). "
                f"The rest is in less liquid forms (deposits, real estate, debts).",
            ),
            "relevance": None,
        })

    # 4. Geo concentration (if exposure available)
    if geo and geo.get("exposure"):
        top_geo = next((r for r in geo["exposure"] if r.get("pct")), None)
        if top_geo:
            insights.append({
                "title": L(
                    f"Страновая концентрация: {top_geo['country']} {top_geo['pct']}%",
                    f"Country concentration: {top_geo['country']} {top_geo['pct']}%",
                ),
                "category": "risk",
                "body": L(
                    f"Наибольшая доля капитала размещена в стране {top_geo['country']} "
                    f"({top_geo['pct']}%). Географическая концентрация связывает капитал "
                    f"с локальными экономическими и регуляторными условиями.",
                    f"The largest share of capital sits in {top_geo['country']} "
                    f"({top_geo['pct']}%). Geographic concentration ties your wealth to "
                    f"local economic and regulatory conditions.",
                ),
                "relevance": top_geo["country"],
            })

    return insights[:6]


def demo_geo_observations(exposure: list[dict[str, Any]]) -> list[dict[str, str]]:
    obs: list[dict[str, str]] = []
    ranked = [r for r in exposure if r.get("pct")]
    if not ranked:
        return obs
    top = ranked[0]
    obs.append({
        "region": top["country"],
        "note": f"{top['pct']}% капитала сосредоточено в стране {top['country']}.",
    })
    if len(ranked) == 1:
        obs.append({
            "region": "global",
            "note": "Капитал размещён в одной юрисдикции — географической диверсификации нет.",
        })
    else:
        obs.append({
            "region": "global",
            "note": f"Капитал распределён между {len(ranked)} странами.",
        })
    return obs


# --- Scenario (heuristic) -----------------------------------------------------

def demo_scenario_changes(scenario_text: str, assets: list[AssetItem]) -> dict[str, Any]:
    """Return a dict matching ScenarioChanges (changes + summary)."""
    text = (scenario_text or "").strip()
    if not text:
        return {"changes": [], "summary": "Пустой сценарий."}

    low = text.lower()
    changes: list[dict[str, Any]] = []

    # "продам/избавлюсь от <crypto>" -> remove
    for kw, sym in _CRYPTO_WORDS.items():
        if kw in low and any(w in low for w in ("прода", "избав", "выйду", "sell")):
            changes.append({"action": "remove", "asset_type": "crypto", "target": sym,
                            "note": f"Продажа {sym} (демо)"})

    # "куплю/добавлю <amount> <ccy>" -> add
    if any(w in low for w in ("купл", "добав", "вложу", "buy")):
        num_match = _NUM_RE.search(text)
        amount = _expand_amount(num_match.group(1), num_match.group(2)) if num_match else Decimal(1000)
        currency = "USD"
        for kw, code in _CCY_WORDS.items():
            if kw in low:
                currency = code
                break
        changes.append({"action": "add", "asset_type": "cash", "amount": str(amount),
                        "currency": currency, "note": f"Покупка {currency} (демо)"})

    summary = "Демо-режим: эвристический разбор сценария." if changes else \
        "Демо-режим: не удалось распознать изменения в сценарии."
    return {"changes": changes, "summary": summary}


# --- Vision / STT canned ------------------------------------------------------

DEMO_VISION_TEXT = (
    "наличные 5000 USD\n"
    "вклад 8000 EUR в банке\n"
    "0.5 BTC на кошельке\n"
    "акции на 3000 USD"
)

DEMO_TRANSCRIPT = "десять тысяч евро наличными в минске и два биткоина на кошельке"
