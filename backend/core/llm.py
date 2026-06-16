"""Provider-agnostic LLM access layer.

Every agent talks to models *only* through this module. Swapping a provider is
a one-line change in `.env` (the LLM role registry) — no code changes.

Roles -> "provider:model" mapping lives in Settings (config.py). We build a
LangChain BaseChatModel via init_chat_model, which gives a uniform interface
(invoke / ainvoke / with_structured_output / tool calling) across Anthropic,
OpenAI, Google, Groq, Ollama, etc.
"""
from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import TypeVar

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from core.config import settings


class ModelRole(str, Enum):
    """Logical назначения; маппятся на провайдер:модель из .env."""

    PARSER = "parser"
    ADVISOR = "advisor"
    VISION = "vision"
    SUPERVISOR = "supervisor"
    NEWS_RANK = "news_rank"
    SCENARIO = "scenario"


# Providers that support Anthropic-style prompt caching via cache_control.
_CACHE_CAPABLE_PROVIDERS = {"anthropic"}


def _split_spec(spec: str) -> tuple[str, str]:
    """"anthropic:claude-sonnet-4-6" -> ("anthropic", "claude-sonnet-4-6")."""
    if ":" not in spec:
        raise ValueError(
            f"Invalid LLM spec '{spec}'. Expected '<provider>:<model>'."
        )
    provider, model = spec.split(":", 1)
    return provider.strip(), model.strip()


def _api_key_for(provider: str) -> str | None:
    return {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "google_genai": settings.google_api_key,
        "groq": settings.groq_api_key,
    }.get(provider) or None


@lru_cache(maxsize=None)
def get_model(role: ModelRole, temperature: float = 0.0) -> BaseChatModel:
    """Return a configured chat model for a role (cached per role+temperature)."""
    spec = settings.llm_for(role.value)
    provider, model = _split_spec(spec)

    kwargs: dict = {"model": model, "model_provider": provider, "temperature": temperature}
    api_key = _api_key_for(provider)
    if api_key:
        kwargs["api_key"] = api_key

    return init_chat_model(**kwargs)


TSchema = TypeVar("TSchema", bound=BaseModel)


def structured(role: ModelRole, schema: type[TSchema], temperature: float = 0.0) -> Runnable:
    """Model that returns an instance of `schema` (Pydantic) reliably.

    Uses LangChain's with_structured_output, which selects tool-calling or
    JSON mode per provider — uniform structured output everywhere.
    """
    return get_model(role, temperature).with_structured_output(schema)


def supports_prompt_caching(role: ModelRole) -> bool:
    provider, _ = _split_spec(settings.llm_for(role.value))
    return provider in _CACHE_CAPABLE_PROVIDERS
