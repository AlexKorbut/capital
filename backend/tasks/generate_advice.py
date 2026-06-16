"""Celery task: weekly batch advice generation (plan §8, Срез 8).

Runs the advisor graph for every user who has a confirmed portfolio and hasn't
already received advice this week. The graph compiles once against the durable
(Postgres) checkpointer, then runs per user with its own thread id.

Errors are isolated per user — one failure never aborts the batch.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from agents import runners
from agents.checkpointer import checkpointer_context
from agents.graph import compile_all
from core.db import SessionLocal
from core.tiers import _count_advice_this_week, limits_for
from models.snapshot import Snapshot
from models.user import User
from tasks.celery_app import celery
from tasks.runner import run

logger = logging.getLogger("kapital.tasks.advice")


async def _eligible_user_ids() -> list[str]:
    """Users with a confirmed snapshot who are under their weekly advice quota."""
    async with SessionLocal() as db:
        users = list(await db.scalars(select(User)))
        eligible: list[str] = []
        for user in users:
            has_snapshot = await db.scalar(
                select(Snapshot.id)
                .where(Snapshot.user_id == user.id, Snapshot.is_confirmed.is_(True))
                .limit(1)
            )
            if not has_snapshot:
                continue
            limits = limits_for(user)
            if limits.advice_per_week is not None:
                used = await _count_advice_this_week(db, user.id)
                if used >= limits.advice_per_week:
                    continue
            eligible.append(str(user.id))
        return eligible


async def _generate() -> int:
    user_ids = await _eligible_user_ids()
    if not user_ids:
        logger.info("generate_due_advice: no eligible users")
        return 0

    generated = 0
    async with checkpointer_context() as checkpointer:
        graphs = compile_all(checkpointer)
        for uid in user_ids:
            thread_id = f"advice-batch-{uuid.uuid4()}"
            try:
                result = await runners.run_advisor(
                    graphs, thread_id, {"user_id": uid}
                )
                if result.get("error"):
                    logger.warning("advice for %s errored: %s", uid, result["error"])
                    continue
                generated += 1
            except Exception as e:  # noqa: BLE001 — isolate per-user failures
                logger.warning("advice for %s crashed: %s", uid, e)
    logger.info("generate_due_advice: generated %d sessions", generated)
    return generated


@celery.task(name="tasks.generate_advice.generate_due_advice")
def generate_due_advice() -> int:
    return run(_generate())
