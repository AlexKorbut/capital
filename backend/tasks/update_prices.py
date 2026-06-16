"""Celery tasks: warm FX rates and crypto prices into the cache + DB.

These wrap the same coroutines the dev APScheduler calls
(``services.market.refresh_fx`` / ``refresh_crypto``), so dev and prod exercise
identical code — only the trigger differs.
"""
from __future__ import annotations

import logging

from services import market
from tasks.celery_app import celery
from tasks.runner import run

logger = logging.getLogger("kapital.tasks.prices")


@celery.task(name="tasks.update_prices.refresh_fx_rates")
def refresh_fx_rates() -> str:
    run(market.refresh_fx())
    logger.info("refresh_fx_rates done")
    return "ok"


@celery.task(name="tasks.update_prices.refresh_crypto_prices")
def refresh_crypto_prices() -> str:
    run(market.refresh_crypto())
    logger.info("refresh_crypto_prices done")
    return "ok"
