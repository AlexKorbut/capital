"""Legal document schema."""
from __future__ import annotations

from pydantic import BaseModel


class LegalDoc(BaseModel):
    slug: str
    title: str
    markdown: str
