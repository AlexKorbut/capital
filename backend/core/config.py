"""Application configuration via pydantic-settings.

All runtime knobs (DB, LLM role registry, payments, email, observability) are
read from environment / .env so that dev (Windows, no Docker) and prod
(VPS + Docker) differ only by configuration.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Well-known placeholder shipped in .env.example. Refused in production so a
# deploy can never run with a forgeable, publicly-known signing key.
DEFAULT_JWT_SECRET = "change_me_min_32_chars_long_random_string_here"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Environment ---
    environment: Literal["development", "production"] = "development"
    debug: bool = True

    # --- API / cookies ---
    # Mount prefix for all routers (kept in one place so the refresh-cookie path
    # can't drift out of sync with the actual route URLs).
    api_prefix: str = "/api/v1"
    # Refresh-cookie SameSite. "lax" is correct/secure for the default
    # same-origin deploy; set "none" (implies Secure) for a cross-origin API.
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # --- Demo mode ---
    # When on, LLM-backed services return realistic canned/computed results
    # instead of calling a provider — so the whole app is testable with NO API
    # keys. Auto-enabled in development when no provider key is set (see
    # `is_demo`). Force on/off with DEMO_MODE in .env.
    demo_mode: bool = False

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///./kapital.db"

    # --- Auth ---
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_min: int = 30
    refresh_token_ttl_days: int = 30
    verify_token_ttl_hours: int = 48
    reset_token_ttl_min: int = 30
    google_client_id: str = ""
    google_client_secret: str = ""

    # --- Field encryption (Fernet) ---
    fernet_key: str = ""

    # --- LLM role registry (provider-agnostic) ---
    llm_parser: str = "anthropic:claude-sonnet-4-6"
    llm_advisor: str = "anthropic:claude-sonnet-4-6"
    llm_vision: str = "anthropic:claude-sonnet-4-6"
    llm_supervisor: str = "anthropic:claude-haiku-4-5"
    llm_news_rank: str = "anthropic:claude-haiku-4-5"
    llm_scenario: str = "anthropic:claude-haiku-4-5"

    # Provider keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    groq_api_key: str = ""

    # --- Speech-to-text ---
    stt_provider: Literal["openai", "local", "groq"] = "openai"
    stt_language: str = "ru"

    # --- Market data ---
    alpha_vantage_api_key: str = ""
    finnhub_api_key: str = ""
    coingecko_api_key: str = ""
    news_api_key: str = ""

    # --- Payments ---
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_pro_monthly: str = ""
    stripe_price_pro_yearly: str = ""
    stripe_price_business_monthly: str = ""
    coingate_api_token: str = ""
    coingate_webhook_secret: str = ""
    coingate_sandbox: bool = True
    # Crypto is one-shot, so we sell a prepaid Pro period instead of a recurring
    # subscription: each successful CoinGate payment grants this many months.
    crypto_pro_price_usd: str = "9.00"
    crypto_pro_months: int = 1

    # Public base URL of the SPA — Stripe/CoinGate redirect users back here after
    # checkout (e.g. https://kapital.app). Dev defaults to the Vite server.
    public_url: str = "http://localhost:5173"

    # --- Email ---
    email_provider: Literal["console", "resend", "postmark", "ses"] = "console"
    email_from: str = "noreply@kapital.app"
    resend_api_key: str = ""

    # --- Redis / Celery ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Observability ---
    sentry_dsn: str = ""
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "kapital-dev"

    # --- CORS ---
    cors_origins: str = "http://localhost:5173"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_prod(self) -> bool:
        return self.environment == "production"

    @property
    def llm_keys_present(self) -> bool:
        """True if at least one LLM provider key is configured."""
        return bool(
            self.anthropic_api_key
            or self.openai_api_key
            or self.google_api_key
            or self.groq_api_key
        )

    @property
    def is_demo(self) -> bool:
        """Run LLM-backed services as deterministic stubs (no API keys needed).

        Explicit ``DEMO_MODE`` wins everywhere. Otherwise demo is auto-enabled
        only in development when no provider key is set — production with missing
        keys is a misconfiguration we want to surface, not silently fake.
        """
        if self.demo_mode:
            return True
        return not self.is_prod and not self.llm_keys_present

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def llm_for(self, role: str) -> str:
        """Return the configured 'provider:model' string for a role name."""
        return getattr(self, f"llm_{role}")

    def production_misconfigurations(self) -> list[str]:
        """Fatal security misconfigurations for a production deploy.

        These secrets otherwise silently fall back to insecure no-ops or a
        well-known default (forgeable JWTs, plaintext-at-rest, unverified
        webhooks). In production we refuse to boot rather than run insecurely.
        """
        problems: list[str] = []
        if self.jwt_secret == DEFAULT_JWT_SECRET or len(self.jwt_secret) < 32:
            problems.append(
                "JWT_SECRET must be a unique value of at least 32 chars "
                "(the default placeholder is public)."
            )
        if not self.fernet_key:
            problems.append(
                "FERNET_KEY must be set so field-level encryption at rest is active."
            )
        if self.stripe_secret_key and not self.stripe_webhook_secret:
            problems.append(
                "STRIPE_WEBHOOK_SECRET must be set when Stripe is configured."
            )
        if self.coingate_api_token and not self.coingate_webhook_secret:
            problems.append(
                "COINGATE_WEBHOOK_SECRET must be set when CoinGate is configured."
            )
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
