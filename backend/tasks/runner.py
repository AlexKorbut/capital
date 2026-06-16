"""Run an async coroutine from a synchronous Celery worker.

Celery tasks are plain sync callables, but our services are async (httpx, async
SQLAlchemy, LangGraph). Each task body is a coroutine factory; we spin a fresh
event loop per call so tasks stay isolated and there's no loop reuse across the
prefork worker pool.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


def run(coro: Awaitable[T]) -> T:
    """Execute ``coro`` to completion on a new event loop and return its result."""
    return asyncio.run(coro)  # type: ignore[arg-type]
