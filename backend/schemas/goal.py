"""Goal schemas (Feature #5)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class GoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    target_usd: Decimal = Field(gt=0)
    target_date: Optional[date] = None


class GoalOut(BaseModel):
    id: str
    title: str
    target_usd: str
    target_date: Optional[str] = None
    current_usd: str
    remaining_usd: str
    progress_pct: Optional[str] = None
    achieved: bool = False
    monthly_growth_usd: Optional[str] = None
    projected_date: Optional[str] = None
    created_at: Optional[str] = None
