"""Async SQLAlchemy engine + session.

Works with SQLite (dev, no Docker) and PostgreSQL (prod) — the only difference
is DATABASE_URL. Models use portable column types (see models/types.py).
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from core.config import settings

# SQLite needs check_same_thread=False for async usage.
_connect_args = {"check_same_thread": False} if settings.is_sqlite else {}

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug and not settings.is_prod,
    future=True,
    connect_args=_connect_args,
    pool_pre_ping=not settings.is_sqlite,
)

if settings.is_sqlite:
    # SQLite disables foreign-key enforcement by default, which silently turns
    # every ondelete="CASCADE" into a no-op (orphaned rows, GDPR-delete leaks).
    # Re-enable it on every new DBAPI connection.
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fk(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async DB session."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
