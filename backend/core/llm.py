"""Provider-agnostic LLM access layer.

Every agent talks to models *only* through this module. Swapping a provider is
a one-line change in `.env` (the LLM role registry) — no code changes.

Roles -> "provider:model" mapping lives in Settings (config.py). We build a
LangChain BaseChatModel via init_chat_model, which gives a uniform interface
(invoke / ainvoke / with_structured_output / tool calling) across Anthropic,
OpenAI, Google, Groq, Ollama, etc.

Bring-your-own-keys (BYOK)
--------------------------
A user may store their own provider keys (Settings → AI) and switch the account
to ``mode = "own"``. The current request's user AI config is published through
a context variable by the auth dependency (``core.deps.get_current_user``), so
services do not thread keys through every call:

* ``api_key_for(provider)`` — the key that applies right now: the user's own
  key in ``own`` mode, the server key in ``kapital`` mode (default).
* ``is_demo_for(role)`` — whether a role runs as a deterministic stub for this
  request. In ``own`` mode a missing user key means "stub", never a silent
  fallback to the server's paid keys.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Any, Literal, Mapping, TypeVar

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from core.config import settings
from core.crypto import decrypt


class ModelRole(str, Enum):
    """Logical назначения; маппятся на провайдер:модель из .env."""

    PARSER = "parser"
    ADVISOR = "advisor"
    VISION = "vision"
    SUPERVISOR = "supervisor"
    NEWS_RANK = "news_rank"
    SCENARIO = "scenario"


# Providers a user may bring their own key for (BYOK). Order = UI order.
PROVIDERS: dict[str, str] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google_genai": "Google",
    "groq": "Groq",
}

# Providers that need no API key at all (local inference).
KEYLESS_PROVIDERS = {"ollama"}

# Providers that support Anthropic-style prompt caching via cache_control.
_CACHE_CAPABLE_PROVIDERS = {"anthropic"}

AiMode = Literal["kapital", "own"]
AI_MODES: tuple[str, ...] = ("kapital", "own")


@dataclass(frozen=True)
class UserAi:
    """Per-user AI configuration for the current request (decrypted)."""

    mode: str = "kapital"
    keys: Mapping[str, str] = field(default_factory=dict)  # provider -> plaintext


_user_ai: ContextVar[UserAi | None] = ContextVar("kapital_user_ai", default=None)


def set_user_ai(ai: UserAi | None) -> Token[UserAi | None]:
    """Publish the request's user AI config (called by the auth dependency)."""
    return _user_ai.set(ai)


def reset_user_ai(token: Token[UserAi | None]) -> None:
    _user_ai.reset(token)


def current_user_ai() -> UserAi | None:
    return _user_ai.get()


def user_ai_from_settings(user_settings: Mapping[str, Any] | None) -> UserAi:
    """Decrypt ``settings["ai"]`` of a user row into a ``UserAi``.

    Stored shape: ``{"ai": {"mode": "own", "keys": {"anthropic": "<fernet>"}}}``.
    Unknown providers, empty values and unknown modes are ignored.
    """
    cfg = (user_settings or {}).get("ai") or {}
    mode = cfg.get("mode") if cfg.get("mode") in AI_MODES else "kapital"
    keys: dict[str, str] = {}
    for provider, enc in (cfg.get("keys") or {}).items():
        if provider in PROVIDERS and enc:
            plain = decrypt(enc)
            if plain:
                keys[provider] = plain
    return UserAi(mode=mode, keys=keys)


def mask_key(key: str) -> str:
    """"…wxyz" — enough to recognise a key, never enough to use it."""
    tail = key[-4:] if len(key) >= 8 else ""
    return f"…{tail}" if tail else "…"


def _split_spec(spec: str) -> tuple[str, str]:
    """"anthropic:claude-sonnet-4-6" -> ("anthropic", "claude-sonnet-4-6")."""
    if ":" not in spec:
        raise ValueError(
            f"Invalid LLM spec '{spec}'. Expected '<provider>:<model>'."
        )
    provider, model = spec.split(":", 1)
    return provider.strip(), model.strip()


def _server_key_for(provider: str) -> str | None:
    return {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "google_genai": settings.google_api_key,
        "groq": settings.groq_api_key,
    }.get(provider) or None


def api_key_for(provider: str) -> str | None:
    """The provider key that applies to the current request.

    ``own`` mode uses only the user's keys; ``kapital`` mode (or no user
    context, e.g. background jobs) uses the server keys.
    """
    ai = _user_ai.get()
    if ai is not None and ai.mode == "own":
        return ai.keys.get(provider) or None
    return _server_key_for(provider)


def provider_for(role: ModelRole) -> str:
    provider, _ = _split_spec(settings.llm_for(role.value))
    return provider


def llm_available(role: ModelRole) -> bool:
    """True when the role's provider can be called under the current context."""
    provider = provider_for(role)
    if provider in KEYLESS_PROVIDERS:
        return True
    return bool(api_key_for(provider))


def is_demo_for(role: ModelRole) -> bool:
    """Run this role as a deterministic stub for the current request?

    * Explicit ``DEMO_MODE`` wins everywhere.
    * ``own`` mode: stub exactly when the user has no key for the role's
      provider — the server's keys are never used behind the user's back.
    * ``kapital`` mode: as before — auto-stub only in development when the
      key is missing; production with a missing key must surface as an error.
    """
    if settings.demo_mode:
        return True
    ai = _user_ai.get()
    if ai is not None and ai.mode == "own":
        return not llm_available(role)
    return not settings.is_prod and not llm_available(role)


@lru_cache(maxsize=64)
def _build_model(
    provider: str, model: str, temperature: float, api_key: str | None
) -> BaseChatModel:
    kwargs: dict = {"model": model, "model_provider": provider, "temperature": temperature}
    if api_key:
        kwargs["api_key"] = api_key
    return init_chat_model(**kwargs)


def get_model(role: ModelRole, temperature: float = 0.0) -> BaseChatModel:
    """Return a configured chat model for a role under the current context.

    Cached per (provider, model, temperature, key): two users with different
    keys get different clients; the same user gets the same client back.
    """
    provider, model = _split_spec(settings.llm_for(role.value))
    return _build_model(provider, model, temperature, api_key_for(provider))


TSchema = TypeVar("TSchema", bound=BaseModel)


def structured(role: ModelRole, schema: type[TSchema], temperature: float = 0.0) -> Runnable:
    """Model that returns an instance of `schema` (Pydantic) reliably.

    Uses LangChain's with_structured_output, which selects tool-calling or
    JSON mode per provider — uniform structured output everywhere.
    """
    return get_model(role, temperature).with_structured_output(schema)


def supports_prompt_caching(role: ModelRole) -> bool:
    return provider_for(role) in _CACHE_CAPABLE_PROVIDERS
