"""Celery application + beat schedule (prod background jobs).

In prod (Docker) these replace the in-process APScheduler used in dev. One role
per environment — see ``main.py``: the in-process scheduler is skipped when
``ENVIRONMENT=production`` so jobs aren't double-run.

Broker + result backend are the same Redis instance used for caching.

    worker:  celery -A tasks.celery_app.celery worker --loglevel=info
    beat:    celery -A tasks.celery_app.celery beat   --loglevel=info
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from core.config import settings

celery = Celery(
    "kapital",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "tasks.update_prices",
        "tasks.fetch_news",
        "tasks.generate_advice",
    ],
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,  # re-queue if a worker dies mid-task
    worker_max_tasks_per_child=200,  # guard against slow leaks
    broker_connection_retry_on_startup=True,
)

# Beat schedule — mirrors the dev APScheduler cadence (market.refresh_*), plus
# the heavier news/advice jobs that dev runs on demand.
celery.conf.beat_schedule = {
    "refresh-crypto-every-5-min": {
        "task": "tasks.update_prices.refresh_crypto_prices",
        "schedule": 300.0,
    },
    "refresh-fx-hourly": {
        "task": "tasks.update_prices.refresh_fx_rates",
        "schedule": crontab(minute=5),  # :05 every hour
    },
    "fetch-news-every-6h": {
        "task": "tasks.fetch_news.refresh_news",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    "generate-weekly-advice": {
        "task": "tasks.generate_advice.generate_due_advice",
        "schedule": crontab(minute=0, hour=8, day_of_week=1),  # Mon 08:00 UTC
    },
}
