"""Cache abstraction. Dev: in-memory TTL. Prod: Redis.

Used by market_service for FX/price caching. The active backend is selected by
environment (``settings.is_prod`` + ``redis_url``) so callers never change.
"""
from __future__ import annotations

import json
import logging
import time
from decimal import Decimal
from typing import Any, Protocol

logger = logging.getLogger("kapital.cache")


class Cache(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: int) -> None: ...
    async def delete(self, key: str) -> None: ...


class InMemoryTTLCache:
    """Simple process-local TTL cache for dev (no Redis needed)."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    async def get(self, key: str) -> Any | None:
        item = self._store.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int) -> None:
        self._store[key] = (time.monotonic() + ttl, value)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


# --- JSON codec (Decimal-aware) ------------------------------------------
# Market values are ``Decimal``; JSON has no decimal type, so we tag them.

def _encode(value: Any) -> str:
    def default(o: Any) -> Any:
        if isinstance(o, Decimal):
            return {"__decimal__": str(o)}
        raise TypeError(f"Not JSON-serializable: {type(o)}")

    return json.dumps(value, default=default)


def _decode(raw: str) -> Any:
    def hook(d: dict) -> Any:
        if "__decimal__" in d:
            return Decimal(d["__decimal__"])
        return d

    return json.loads(raw, object_hook=hook)


class RedisCache:
    """Redis-backed TTL cache (prod). Values are JSON (Decimal-aware)."""

    def __init__(self, url: str) -> None:
        # Imported lazily so dev never needs redis installed/running.
        from redis import asyncio as aioredis

        self._redis = aioredis.from_url(url, encoding="utf-8", decode_responses=True)

    async def get(self, key: str) -> Any | None:
        raw = await self._redis.get(key)
        return _decode(raw) if raw is not None else None

    async def set(self, key: str, value: Any, ttl: int) -> None:
        await self._redis.set(key, _encode(value), ex=ttl)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)


_cache: Cache | None = None


def get_cache() -> Cache:
    """Return the active cache backend (Redis in prod, in-memory in dev)."""
    global _cache
    if _cache is None:
        from core.config import settings

        if settings.is_prod and settings.redis_url:
            try:
                _cache = RedisCache(settings.redis_url)
                logger.info("Cache backend: Redis (%s)", settings.redis_url)
            except Exception as e:  # noqa: BLE001 — never hard-fail on cache
                logger.warning("Redis unavailable (%s); using in-memory cache", e)
                _cache = InMemoryTTLCache()
        else:
            _cache = InMemoryTTLCache()
    return _cache
