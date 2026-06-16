"""Durable checkpointer factory (human-in-the-loop survives restarts).

dev  -> AsyncSqliteSaver  (same SQLite file, no Docker/Redis needed)
prod -> AsyncPostgresSaver

Both are async resources, so they're entered as an async context manager during
the app lifespan (see `main.py`). For unit tests, `MemorySaver` is enough.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from core.config import settings


def _sqlite_path(database_url: str) -> str:
    """Extract the file path from a `sqlite+aiosqlite:///./kapital.db` URL."""
    # everything after the scheme's '///'
    tail = database_url.split(":///", 1)[-1]
    return tail or "kapital.db"


@asynccontextmanager
async def checkpointer_context() -> AsyncIterator[object]:
    """Yield a ready-to-use (set-up) LangGraph checkpointer."""
    if settings.is_sqlite:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        path = _sqlite_path(settings.database_url)
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            yield saver
    else:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(settings.database_url) as saver:
            await saver.setup()
            yield saver
