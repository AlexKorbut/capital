"""File Processor service (Agent #4).

Turns an uploaded file into plain text that the Parser agent (#5) can then
structure. Two paths:

  * Spreadsheets (xlsx/xls/csv) -> a compact textual dump via pandas/openpyxl.
  * Images (screenshots of banking/brokerage apps, photos) -> a textual asset
    listing via the provider-agnostic vision model (``LLM_VISION``).

Provider-agnostic: the image path goes through ``core.llm.get_model`` only, so
swapping Anthropic for OpenAI/Google is a one-line ``.env`` change.
"""
from __future__ import annotations

import base64
import io
import logging

from core.config import settings
from core.llm import ModelRole, get_model

logger = logging.getLogger("kapital.file_processor")

_IMAGE_MIME = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_EXCEL_EXT = (".xlsx", ".xls")
_CSV_EXT = (".csv",)

_VISION_SYSTEM = """\
Ты — ассистент, который ИЗВЛЕКАЕТ из изображения список активов пользователя
(скриншот банковского/брокерского приложения, фото записей, таблица).
Верни ПРОСТОЙ ТЕКСТ — по одной строке на актив: тип, сумма, валюта/тикер, место.
Не добавляй советов и выводов — только то, что видно на изображении. Если данных
нет — верни пустую строку."""

_VISION_HUMAN = "Извлеки из этого изображения все активы в виде простого списка."


def _flatten_content(content) -> str:
    """LangChain message content may be str or a list of blocks — flatten it."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            else:
                parts.append(str(block))
        return "\n".join(p for p in parts if p)
    return str(content or "")


async def _image_to_text(raw_bytes: bytes, mime_type: str) -> str:
    if settings.is_demo:
        from core.demo import DEMO_VISION_TEXT
        return DEMO_VISION_TEXT
    b64 = base64.b64encode(raw_bytes).decode("ascii")
    model = get_model(ModelRole.VISION)
    messages = [
        ("system", _VISION_SYSTEM),
        (
            "human",
            [
                {"type": "text", "text": _VISION_HUMAN},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                },
            ],
        ),
    ]
    resp = await model.ainvoke(messages)
    return _flatten_content(getattr(resp, "content", resp)).strip()


def _excel_to_text(raw_bytes: bytes) -> str:
    import pandas as pd

    sheets = pd.read_excel(io.BytesIO(raw_bytes), sheet_name=None, dtype=str)
    chunks: list[str] = []
    for name, frame in sheets.items():
        frame = frame.fillna("")
        chunks.append(f"# Лист: {name}\n{frame.to_csv(index=False)}")
    return "\n\n".join(chunks).strip()


def _csv_to_text(raw_bytes: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw_bytes.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", "ignore").strip()


async def process_file(
    raw_bytes: bytes, mime_type: str | None = None, filename: str = ""
) -> str:
    """Extract plain text from an uploaded file (image / spreadsheet / csv)."""
    if not raw_bytes:
        return ""

    name = (filename or "").lower()
    mime = (mime_type or "").lower()

    if mime in _IMAGE_MIME or name.endswith(_IMAGE_EXT):
        return await _image_to_text(raw_bytes, mime or "image/png")

    if name.endswith(_EXCEL_EXT) or "spreadsheet" in mime or "excel" in mime:
        return _excel_to_text(raw_bytes)

    if name.endswith(_CSV_EXT) or "csv" in mime:
        return _csv_to_text(raw_bytes)

    # Unknown type — best-effort decode as UTF-8 text.
    try:
        return raw_bytes.decode("utf-8").strip()
    except UnicodeDecodeError:
        return raw_bytes.decode("utf-8", "ignore").strip()
