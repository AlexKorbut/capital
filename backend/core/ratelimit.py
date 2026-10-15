"""Rate limiting (slowapi). In-memory in dev; Redis-backed in prod.

The same decorators work in both environments — only the storage backend
changes via config. Limits target abuse-prone surfaces (auth, input, advice)
without throttling normal dashboard reads.
"""
from __future__ import annotations

from slowapi import Limiter
from starlette.requests import Request
from slowapi.util import get_remote_address

from core.config import settings


def _storage_uri() -> str | None:
    # Prod shares the Redis instance used for cache/Celery; dev uses memory.
    if settings.is_prod and settings.redis_url:
        return settings.redis_url
    return None  # in-memory


limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_storage_uri(),
    default_limits=[],  # opt-in per route; no global cap
    # headers_enabled would inject X-RateLimit-* into responses, but slowapi can
    # only do that when the endpoint exposes a `response: Response` param. Ours
    # return Pydantic models, so enabling it raises on every limited call. The
    # 429 itself is still emitted by the RateLimitExceeded handler regardless.
    headers_enabled=False,
    # slowapi otherwise auto-reads ".env" via starlette Config, which can blow up
    # on non-UTF-8 dev env files. We read config ourselves, so point it away.
    config_filename="__kapital_no_slowapi_env__",
)

# Named limits referenced by route decorators (tune in one place).
AUTH_LIMIT = "10/minute"
FORGOT_LIMIT = "5/hour"
INPUT_LIMIT = "30/minute"
ADVICE_LIMIT = "10/minute"

def user_key(request: Request) -> str:
    """Rate-limit key for authenticated routes: the user (a JWT session and a personal
    ``kp_`` token are the same bucket), falling back to the IP before authentication.
    A host such as Jarvis calls from one address for all its users -- keying by IP would
    make them share a single limit."""
    return getattr(request.state, "rate_key", None) or get_remote_address(request)


# Finance projects (fin-01). Writes can fan out to external FX providers; per user.
FINANCE_WRITE_LIMIT = "60/minute"   # POST/PATCH of projects and expenses
FINANCE_FX_LIMIT = "60/minute"      # GET /finance/fx
FINANCE_REVALUE_LIMIT = "10/minute"  # POST /finance/projects/{id}/revalue

# Receipt photos (fin-02). Each upload can carry up to 10 MB: keep it tighter than plain writes.
ATTACHMENT_UPLOAD_LIMIT = "20/minute"  # POST /finance/expenses/{id}/attachments, per user
ATTACHMENT_READ_LIMIT = "120/minute"  # GET /finance/attachments/{id}, per user (a gallery of pictures)
