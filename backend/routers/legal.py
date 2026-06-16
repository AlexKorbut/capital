"""Legal documents — serve ToS / Privacy / Disclaimer as markdown (public).

Single source of truth lives in ``backend/legal/*.md`` so the same text backs the
SPA pages, emails and any printed copy.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from schemas.legal import LegalDoc

router = APIRouter(prefix="/legal", tags=["legal"])

_LEGAL_DIR = Path(__file__).resolve().parent.parent / "legal"

# slug -> {lang: (title, filename)}. English falls back to Russian if absent.
_DOCS = {
    "tos": {
        "ru": ("Условия использования", "tos.md"),
        "en": ("Terms of Service", "tos_en.md"),
    },
    "privacy": {
        "ru": ("Политика конфиденциальности", "privacy.md"),
        "en": ("Privacy Policy", "privacy_en.md"),
    },
    "disclaimer": {
        "ru": ("Финансовый дисклеймер", "disclaimer.md"),
        "en": ("Financial Disclaimer", "disclaimer_en.md"),
    },
}


@lru_cache
def _read(filename: str) -> str:
    path = _LEGAL_DIR / filename
    return path.read_text(encoding="utf-8")


@router.get("/{slug}", response_model=LegalDoc)
async def get_legal(slug: str, lang: str = Query("ru")) -> LegalDoc:
    by_lang = _DOCS.get(slug)
    if by_lang is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    title, filename = by_lang.get(lang.lower(), by_lang["ru"])
    try:
        content = _read(filename)
    except OSError:
        title, filename = by_lang["ru"]  # EN missing -> Russian original
        try:
            content = _read(filename)
        except OSError:
            raise HTTPException(status_code=500, detail="Документ недоступен")
    return LegalDoc(slug=slug, title=title, markdown=content)
