"""Parser service (Agent #5) — the product's quality core.

Turns free-form multilingual text ("10к евро налик в минске, 2 битка на
кошельке, депозит 1000 лари в боге под 9%") into a structured
``ParsedPortfolio`` of ``AssetItem``s.

Provider-agnostic: it only talks to the LLM through ``core.llm.structured``,
so swapping Anthropic for OpenAI/Google/Ollama is a one-line ``.env`` change.
"""
from __future__ import annotations

from agents.state import ParsedPortfolio
from core.config import settings
from core.llm import ModelRole, structured

# --- Prompt -------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are the parsing engine of a private multi-currency capital tracker.
The user describes their assets in free form, in ANY language (Russian,
English, etc.), often with slang, abbreviations and transliterated place
names. Extract every distinct asset as a structured item.

Rules:
- Output ONLY structured data — never prose.
- One item per distinct asset. Split combined statements into separate items.
- amount: numeric quantity in the asset's OWN currency/unit (use Decimal-safe
  plain numbers, no thousands separators). Expand shorthand: "10к"/"10k" = 10000,
  "2 ляма"/"2кк"/"2m" = 2000000.
- currency: ISO-4217 for fiat (EUR, USD, BYN, GEL, RUB...). For crypto use the
  symbol (BTC, ETH, USDT). Infer from context and language:
    * "евро" -> EUR, "доллар/бакс" -> USD, "рубль" -> RUB,
      "лари" -> GEL, "бел. рубль/зайчик" -> BYN, "тенге" -> KZT.
- asset_type: one of cash | bank_deposit | crypto | stock | real_estate |
  vehicle | debt | other.
    * "машина/авто/тачка/car/vehicle" -> vehicle
    * "налик"/"наличка"/"cash"/"на руках" -> cash
    * "депозит"/"вклад"/"deposit" -> bank_deposit
    * "биток/битки/btc/эфир/eth/крипта" -> crypto
    * "акции/stock/shares" -> stock
    * "квартира/дом/недвижка/apartment/house" -> real_estate
    * money lent/borrowed ("должен мне"/"я должен"/"в долг") -> debt
- For crypto: set ``symbol`` and ``quantity`` (= amount), currency = the symbol.
- For deposits: set ``interest_rate`` if a percent is given ("под 9%" -> 9).
- location/country: capture place hints. Map common transliterations:
    * "минск/minsk" -> location "Minsk", country "BY"
    * "бога/батуми/тбилиси/batumi/tbilisi" (Georgia) -> country "GE"
    * "москва/moscow" -> country "RU"
  ("в боге" is slang for Batumi, Georgia.)
- wallet_address: only if an actual address string is present.
- is_owed_to_me: for debt, true if the money is owed TO the user, false if the
  user owes it.
- confidence: 0..1 — how sure you are about this item. Lower it when the input
  is ambiguous.
- needs_review: set true on the portfolio if ANY item is uncertain or you had
  to guess significantly.
- Do NOT fill usd_value / usd_rate — a later enrichment step does that.

Be precise. Missing or hallucinated assets directly harm the user's net-worth
figure.
"""


# --- Public API ---------------------------------------------------------------


async def parse_text(text: str, base_currency: str = "USD") -> ParsedPortfolio:
    """Parse free-form ``text`` into a ``ParsedPortfolio``.

    Empty input short-circuits to an empty, review-flagged result (no LLM call).
    """
    text = (text or "").strip()
    if not text:
        return ParsedPortfolio(
            assets=[],
            needs_review=True,
            parser_notes="Empty input — nothing to parse.",
        )

    if settings.is_demo:
        from core.demo import demo_parse
        return demo_parse(text, base_currency)

    model = structured(ModelRole.PARSER, ParsedPortfolio)
    messages = [
        ("system", _SYSTEM_PROMPT),
        (
            "human",
            f"User base currency: {base_currency}.\n\nAssets description:\n{text}",
        ),
    ]
    result: ParsedPortfolio = await model.ainvoke(messages)
    return result
