"""Scheduler abstraction.

Dev: APScheduler running inside the FastAPI process (no Docker/Celery needed).
Prod: the same jobs are registered as Celery beat tasks (tasks/celery_app.py).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

Job = Callable[[], Awaitable[None]]


class Scheduler(Protocol):
    def add_interval(self, func: Job, *, seconds: int, job_id: str) -> None: ...
    def add_cron(
        self, func: Job, *, hour: str, minute: str, job_id: str, day_of_week: str | None = None
    ) -> None: ...
    def start(self) -> None: ...
    def shutdown(self) -> None: ...


class APScheduler:
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()

    def add_interval(self, func: Job, *, seconds: int, job_id: str) -> None:
        self._scheduler.add_job(
            func, IntervalTrigger(seconds=seconds), id=job_id, replace_existing=True
        )

    def add_cron(
        self, func: Job, *, hour: str, minute: str, job_id: str, day_of_week: str | None = None
    ) -> None:
        trigger = (
            CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute)
            if day_of_week is not None
            else CronTrigger(hour=hour, minute=minute)
        )
        self._scheduler.add_job(func, trigger, id=job_id, replace_existing=True)

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)


_scheduler: APScheduler | None = None


def get_scheduler() -> APScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = APScheduler()
    return _scheduler
