"""Observability wiring: Sentry (errors) + LangSmith (graph/agent traces).

Both are opt-in via .env and no-op when their keys are absent, so dev stays
quiet by default. Prod sets SENTRY_DSN and LANGCHAIN_* to light them up.

Sentry is initialised once at import-time of `init_observability()` (called
from main.py). LangSmith is configured by exporting the LANGCHAIN_* env vars
that langchain/langgraph read on their own — we just translate our Settings
into those canonical names so the rest of the code never touches them.
"""
from __future__ import annotations

import logging
import os

from core.config import settings

logger = logging.getLogger("kapital.observability")


def _init_sentry() -> bool:
    """Initialise the Sentry SDK if a DSN is configured. Returns True if active."""
    if not settings.sentry_dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - sentry is a prod dep
        logger.warning("SENTRY_DSN set but sentry-sdk is not installed; skipping")
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=os.getenv("GIT_SHA") or None,
        # Performance tracing: sample lightly in prod, fully in dev for visibility.
        traces_sample_rate=0.2 if settings.is_prod else 1.0,
        # Never ship request bodies / headers — portfolios are sensitive (NFR-001).
        send_default_pii=False,
        max_request_body_size="never",
    )
    logger.info("Sentry initialised (env=%s)", settings.environment)
    return True


def _init_langsmith() -> bool:
    """Export LANGCHAIN_* env vars so langchain/langgraph emit traces."""
    if not settings.langchain_tracing_v2 or not settings.langchain_api_key:
        return False
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", settings.langchain_api_key)
    os.environ.setdefault("LANGCHAIN_PROJECT", settings.langchain_project)
    logger.info("LangSmith tracing enabled (project=%s)", settings.langchain_project)
    return True


def init_observability() -> dict[str, bool]:
    """Wire Sentry + LangSmith. Safe to call once at startup; both are opt-in."""
    return {
        "sentry": _init_sentry(),
        "langsmith": _init_langsmith(),
    }
