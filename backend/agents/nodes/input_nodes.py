"""Input-graph nodes (agents #2–#6 + persistence).

Pipeline (always-preview):
    input_router -> (transcribe | process_file | passthrough)
                 -> parse -> validate -> enrich
                 -> human_review (interrupt) -> save_to_db -> END

`human_review` is an interrupt point (see graph.py `interrupt_before`): the
graph pauses *before* it so the user can confirm/edit, then resumes to persist.
"""
from __future__ import annotations

from typing import Any

from agents.state import InputState
from core.config import settings
from services import file_processor as file_processor_service
from services import parser as parser_service
from services import persistence as persistence_service
from services import validator as validator_service

# --- Agent #2: Input Router (no LLM — inspects input_type / magic bytes) -------


async def input_router(state: InputState) -> dict[str, Any]:
    input_type = state.get("input_type", "text")
    return {"trace": [f"input_router: type={input_type}"]}


def route_input(state: InputState) -> str:
    """Conditional edge: pick the decoder for this input type."""
    input_type = state.get("input_type", "text")
    if input_type == "voice":
        return "transcribe"
    if input_type in ("file", "image"):
        return "process_file"
    return "parse"


# --- Agent #3: Whisper Transcriber (voice -> text) ----------------------------


async def transcribe(state: InputState) -> dict[str, Any]:
    """Voice -> text via the provider-agnostic `core.transcription` Transcriber.

    Degrades gracefully: no audio bytes -> pass through any raw_text; no STT key
    configured -> surface a clear error instead of crashing the graph.
    """
    audio = state.get("raw_bytes")
    if not audio:
        transcript = state.get("raw_text", "") or ""
        return {
            "transcript": transcript,
            "raw_text": transcript,
            "trace": ["transcribe: no audio, passthrough"],
        }

    if not settings.openai_api_key:
        return {
            "error": "Транскрипция недоступна: не настроен STT-ключ.",
            "trace": ["transcribe: no STT key"],
        }

    try:
        from core.transcription import get_transcriber

        transcriber = get_transcriber()
        text = await transcriber.transcribe(
            audio, filename=state.get("filename") or "audio.webm"
        )
    except Exception as e:  # noqa: BLE001 — never crash the graph on STT failure
        return {"error": f"Ошибка транскрипции: {e}", "trace": [f"transcribe failed: {e}"]}

    return {
        "transcript": text,
        "raw_text": text,
        "trace": [f"transcribe: {len(text)} chars"],
    }


# --- Agent #4: File Processor (Excel -> text / image -> text via vision) -------


async def process_file(state: InputState) -> dict[str, Any]:
    """Excel/CSV -> text (pandas) or image -> text (LLM_VISION) via service."""
    raw_bytes = state.get("raw_bytes")
    if not raw_bytes:
        text = state.get("raw_text", "") or ""
        return {"raw_text": text, "trace": ["process_file: no bytes, passthrough"]}

    try:
        text = await file_processor_service.process_file(
            raw_bytes,
            mime_type=state.get("mime_type"),
            filename=state.get("filename") or "",
        )
    except Exception as e:  # noqa: BLE001
        return {"error": f"Не удалось обработать файл: {e}", "trace": [f"process_file failed: {e}"]}

    return {"raw_text": text, "trace": [f"process_file: extracted {len(text)} chars"]}


# --- Agent #5: Parser (text -> AssetItem[]) — the product's quality core -------


async def parse(state: InputState) -> dict[str, Any]:
    """Free-form text -> AssetItem[] via the provider-agnostic Parser service.

    Structured imports (input_type="import") arrive already parsed (deterministic
    table importer) — pass them straight through, no LLM.
    """
    if state.get("input_type") == "import":
        seeded = state.get("assets") or []
        return {
            "assets": seeded,
            "needs_review": len(seeded) == 0,
            "trace": [f"parse: imported {len(seeded)} asset(s) (no LLM)"],
        }

    text = state.get("raw_text", "") or ""
    base_currency = state.get("base_currency", "USD") or "USD"
    parsed = await parser_service.parse_text(text, base_currency=base_currency)
    return {
        "assets": parsed.assets,
        "needs_review": parsed.needs_review,
        "trace": [f"parse: {len(parsed.assets)} asset(s)"],
    }


# --- Agent #6: Validator (rules + preview; no LLM) ----------------------------


async def validate(state: InputState) -> dict[str, Any]:
    """Rule-check + normalise the parsed assets (no LLM)."""
    assets = state.get("assets", []) or []
    result = validator_service.validate_assets(assets)
    # validator may normalise currency/symbol in place -> echo assets back.
    return {
        "assets": assets,
        "validation": result,
        "needs_review": state.get("needs_review", False) or result.needs_review,
        "trace": [
            f"validate: valid={result.is_valid} "
            f"review={result.needs_review} "
            f"({len(result.errors)} err, {len(result.warnings)} warn)"
        ],
    }


async def human_review(state: InputState) -> dict[str, Any]:
    """Interrupt target — execution pauses *before* this node so a human can
    edit `assets` via `/portfolio/confirm`, then the graph resumes.
    """
    return {"needs_review": False, "trace": ["human_review: resumed"]}


# --- Persistence: save_to_db --------------------------------------------------


async def save_to_db(state: InputState) -> dict[str, Any]:
    """Persist Snapshot + Asset rows (Decimal). Opens its own DB session."""
    user_id = state.get("user_id")
    if not user_id:
        return {"error": "save_to_db: missing user_id", "trace": ["save_to_db: no user_id"]}

    assets = state.get("assets", []) or []
    snapshot_id = await persistence_service.save_portfolio(
        user_id=user_id,
        assets=assets,
        base_currency=state.get("base_currency", "USD") or "USD",
        raw_input=state.get("raw_text"),
        input_type=state.get("input_type", "text"),
        total_usd=state.get("total_usd"),
    )
    return {
        "snapshot_id": snapshot_id,
        "trace": [f"save_to_db: snapshot {snapshot_id} ({len(assets)} assets)"],
    }
