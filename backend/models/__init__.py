"""Model registry — import every table so Alembic autogenerate discovers them.

Importing this package (`import models`) ensures all mappers are registered
against `core.db.Base.metadata`.
"""
from __future__ import annotations

from core.db import Base

from .advice import AdviceItem, AdviceSession
from .asset import Asset
from .billing import DebtRecord, SubscriptionEvent
from .goal import Goal
from .market import AssetPrice, ExchangeRate
from .news import NewsItem
from .scenario import Scenario
from .snapshot import Snapshot
from .user import User
from .wallet import Wallet

__all__ = [
    "Base",
    "User",
    "Snapshot",
    "Asset",
    "ExchangeRate",
    "AssetPrice",
    "NewsItem",
    "AdviceSession",
    "AdviceItem",
    "Scenario",
    "DebtRecord",
    "SubscriptionEvent",
    "Goal",
    "Wallet",
]
