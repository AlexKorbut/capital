"""KAPITAL backend entrypoint.

Lifespan wires the durable LangGraph checkpointer and compiles all three graphs
once, storing them on `app.state.graphs`. Dev runs in-process (SQLite, no
Docker); prod swaps the adapters behind the same config.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from agents.checkpointer import checkpointer_context
from agents.graph import compile_all
from core.config import settings
from core.observability import init_observability
from core.ratelimit import limiter
from core.scheduler import get_scheduler
from routers import (
    account,
    advice,
    auth,
    billing,
    goals,
    legal,
    portfolio,
    scenarios,
    wallets,
    webhooks,
)
from services import digest, inheritance, market

logging.basicConfig(level=logging.INFO if not settings.debug else logging.DEBUG)
logger = logging.getLogger("kapital")

# Wire Sentry + LangSmith before anything else so startup errors and graph
# compilation traces are captured. Both are opt-in (no-op without their keys).
_OBS = init_observability()

API_PREFIX = settings.api_prefix


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast in production on security-critical misconfiguration (forgeable
    # JWTs, plaintext-at-rest, unverified webhooks) rather than booting insecurely.
    if settings.is_prod:
        problems = settings.production_misconfigurations()
        if problems:
            raise RuntimeError(
                "Refusing to start in production with insecure configuration:\n  - "
                + "\n  - ".join(problems)
            )
    async with AsyncExitStack() as stack:
        checkpointer = await stack.enter_async_context(checkpointer_context())
        app.state.graphs = compile_all(checkpointer)
        logger.info(
            "Graphs compiled (%s) | env=%s | db=%s | demo=%s",
            ", ".join(sorted(app.state.graphs)),
            settings.environment,
            "sqlite" if settings.is_sqlite else "postgres",
            settings.is_demo,
        )
        if settings.is_demo:
            logger.warning(
                "DEMO MODE active — LLM services return canned/computed results "
                "(no provider key set). Set ANTHROPIC_API_KEY etc. for real models."
            )
        # FX / crypto refresh. Dev runs in-process via APScheduler; prod (Docker)
        # runs the same jobs under Celery beat (tasks/), so skip the in-process
        # scheduler there to avoid double-scheduling.
        scheduler = None
        if not settings.is_prod:
            scheduler = get_scheduler()
            scheduler.add_cron(market.refresh_fx, hour="*", minute="5", job_id="fx_refresh")
            scheduler.add_interval(market.refresh_crypto, seconds=300, job_id="crypto_refresh")
            # Lifecycle emails: daily stale-portfolio reminder + weekly digest.
            scheduler.add_cron(
                digest.run_update_reminders, hour="10", minute="0", job_id="update_reminders"
            )
            scheduler.add_cron(
                digest.run_weekly_digest,
                day_of_week="mon", hour="9", minute="0", job_id="weekly_digest",
            )
            scheduler.add_cron(
                inheritance.run_inheritance_check,
                hour="11", minute="0", job_id="inheritance_check",
            )
            scheduler.start()
            logger.info(
                "APScheduler started (dev): fx hourly, crypto 5m, reminders daily, digest weekly"
            )

        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown()
        # AsyncExitStack closes the checkpointer cleanly on shutdown.


app = FastAPI(
    title="KAPITAL API",
    version="0.1.0",
    description="Private multi-currency capital tracker with an AI advisor.",
    lifespan=lifespan,
)

# Rate limiting (slowapi): attach limiter + 429 handler + middleware.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Turn any unhandled error into a 500 with a correlatable incident_id.

    The id is reported to Sentry (when active) so support can look it up, while
    the client never sees a stack trace or sensitive internals (API-spec §errors).
    """
    incident_id = uuid.uuid4().hex
    try:  # tag the Sentry event with the same id we hand to the client
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            scope.set_tag("incident_id", incident_id)
            sentry_sdk.capture_exception(exc)
    except Exception:  # noqa: BLE001 — never let reporting break the response
        pass
    logger.exception("Unhandled error [incident_id=%s] on %s", incident_id, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "internal_server_error", "incident_id": incident_id},
    )


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    """Defense-in-depth headers (Nginx may also set these in prod)."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(self), camera=()"
    )
    if settings.is_prod:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(portfolio.router, prefix=API_PREFIX)
app.include_router(advice.router, prefix=API_PREFIX)
app.include_router(scenarios.router, prefix=API_PREFIX)
app.include_router(billing.router, prefix=API_PREFIX)
app.include_router(webhooks.router, prefix=API_PREFIX)
app.include_router(account.router, prefix=API_PREFIX)
app.include_router(legal.router, prefix=API_PREFIX)
app.include_router(goals.router, prefix=API_PREFIX)
app.include_router(wallets.router, prefix=API_PREFIX)


@app.get("/health", tags=["meta"])
@app.get(f"{API_PREFIX}/health", tags=["meta"])
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "db": "sqlite" if settings.is_sqlite else "postgres",
        "demo_mode": settings.is_demo,
        "graphs": sorted(getattr(app.state, "graphs", {})),
        "observability": _OBS,
    }
