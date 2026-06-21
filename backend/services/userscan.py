"""Memory-safe iteration over the users table for batch/scheduled jobs.

Loading every user with ``select(User)`` holds the whole table in memory and
doesn't scale. These helpers stream users in keyset-paginated batches instead.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User


async def iter_users(
    db: AsyncSession, *, batch_size: int = 500, verified_only: bool = False
) -> AsyncIterator[User]:
    """Yield users in batches ordered by id (keyset pagination).

    Safe to commit between yields — the next batch is fetched with a fresh query.
    """
    last_id = None
    while True:
        stmt = select(User).order_by(User.id).limit(batch_size)
        if verified_only:
            stmt = stmt.where(User.email_verified.is_(True))
        if last_id is not None:
            stmt = stmt.where(User.id > last_id)
        rows = list(await db.scalars(stmt))
        if not rows:
            break
        for user in rows:
            yield user
        last_id = rows[-1].id
        if len(rows) < batch_size:
            break
