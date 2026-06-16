"""Pytest fixtures.

Sets an isolated temp SQLite DB *before* any app module imports bind the engine
to settings.database_url. Tests never touch the dev kapital.db.
"""
from __future__ import annotations

import os
import pathlib
import tempfile

# Must run before `core.config` is imported anywhere.
_TEST_DB = pathlib.Path(tempfile.gettempdir()) / "kapital_test.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB.as_posix()}"
os.environ["ENVIRONMENT"] = "development"
os.environ["FERNET_KEY"] = ""  # encryption no-op in tests

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from core.db import Base, engine  # noqa: E402
import models  # noqa: E402,F401  (register all tables on Base.metadata)


@pytest_asyncio.fixture
async def db_ready():
    """Create all tables on the temp DB; drop them afterwards."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
def _offline_market(monkeypatch):
    """No network in tests: market lookups return None unless a test overrides.

    Tests that need real rates re-patch these via their own monkeypatch.
    """
    import services.market as market

    async def _no_fx(ccy: str):
        return None

    async def _no_crypto(symbol: str):
        return None

    monkeypatch.setattr(market, "usd_rate_for_currency", _no_fx)
    monkeypatch.setattr(market, "usd_price_for_crypto", _no_crypto)
