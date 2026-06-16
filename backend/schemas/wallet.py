"""Wallet schemas (Feature #6)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class WalletCreate(BaseModel):
    chain: str = Field(description="BTC | ETH | TON")
    address: str = Field(min_length=10, max_length=120)
    label: Optional[str] = Field(default=None, max_length=120)


class WalletOut(BaseModel):
    id: str
    chain: str
    address: str
    label: Optional[str] = None
    balance: Optional[str] = None
    usd_value: Optional[str] = None
    created_at: Optional[str] = None


class WalletSyncResult(BaseModel):
    snapshot_id: str
    total_usd: Optional[str] = None
    wallet_count: int
