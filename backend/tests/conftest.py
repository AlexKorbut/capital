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

    # The historical-FX path (services.fx_history) talks to the providers through
    # market._http_get_json directly: fail fast offline instead of reaching the net.
    import httpx

    async def _offline_http(url: str, params: dict | None = None):
        raise httpx.ConnectError("offline in tests")

    async def _offline_bytes(url: str, params: dict | None = None, max_bytes: int | None = None):
        raise httpx.ConnectError("offline in tests")

    monkeypatch.setattr(market, "_http_get_json", _offline_http)
    monkeypatch.setattr(market, "_http_get_bytes", _offline_bytes)


@pytest.fixture(autouse=True)
def _clear_fx_history_cache():
    """The in-process cache outlives a test; keep per-date FX entries from leaking."""
    from core.cache import InMemoryTTLCache, get_cache

    def _clear() -> None:
        cache = get_cache()
        if isinstance(cache, InMemoryTTLCache):
            for key in [k for k in cache._store if k.startswith("fxd:")]:
                cache._store.pop(key, None)

    _clear()
    yield
    _clear()


@pytest.fixture
def fake_fx(monkeypatch):
    """Programmable offline FX providers (see tests/fakes_fx.py)."""
    import services.market as market
    from tests.fakes_fx import FakeFx

    fake = FakeFx()
    monkeypatch.setattr(market, "_http_get_json", fake)
    monkeypatch.setattr(market, "_http_get_bytes", fake.get_bytes)
    return fake


@pytest.fixture(autouse=True)
def uploads_dir(tmp_path, monkeypatch):
    """Every test gets its own empty UPLOADS_DIR: no test ever writes into backend/uploads
    and receipt files never leak from one test into another."""
    from core.config import settings

    target = tmp_path / "uploads"
    monkeypatch.setattr(settings, "uploads_dir", str(target))
    return target


@pytest.fixture
def fernet_on():
    """Turn field encryption on for one test (the suite runs with FERNET_KEY empty).

    Restored by hand, in this order: the Fernet object is cached by ``core.crypto``, so the
    cache has to be cleared AFTER the setting is back to empty, not before."""
    from cryptography.fernet import Fernet

    from core import crypto
    from core.config import settings

    key = Fernet.generate_key().decode()
    previous = settings.fernet_key
    settings.fernet_key = key
    crypto._fernet.cache_clear()
    try:
        yield key
    finally:
        settings.fernet_key = previous
        crypto._fernet.cache_clear()


@pytest.fixture
def fernet_switch():
    """``use(key)`` sets FERNET_KEY in the middle of a test ('' turns encryption off): a file
    written under one key can then be read under another, or under none."""
    from core import crypto
    from core.config import settings

    previous = settings.fernet_key

    def use(key: str) -> None:
        settings.fernet_key = key
        crypto._fernet.cache_clear()

    try:
        yield use
    finally:
        settings.fernet_key = previous
        crypto._fernet.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """slowapi counts per client IP in-process: without a reset, the many users that
    tests register would trip the 10/minute auth limit across test boundaries."""
    from core.ratelimit import limiter

    limiter.reset()
    yield
    limiter.reset()
