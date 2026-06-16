"""Schemas for the advice (AI advisor) endpoints."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class AdviceItemOut(BaseModel):
    id: str
    title: str
    category: Optional[str] = None
    body: str
    relevance: Optional[str] = None
    disclaimer: str
    is_read: bool = False
    created_at: Optional[str] = None


class AdviceSessionOut(BaseModel):
    session_id: str
    snapshot_id: Optional[str] = None
    generated_at: Optional[str] = None
    advice_count: int = 0
    items: list[AdviceItemOut] = Field(default_factory=list)
