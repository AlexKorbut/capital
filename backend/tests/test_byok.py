"""Bring-your-own-keys (BYOK): per-user provider keys and the AI mode switch.

Unit: decrypt/mask helpers, context-aware key resolution, per-role demo
decision, model factory receives the user's key.
HTTP: PUT/GET /account/ai — keys stored encrypted at rest, returned masked,
unknown providers rejected, `demo` reflects the caller's own config.
"""
from __future__ import annotations

import json
import uuid

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

import core.crypto as crypto
import core.llm as llm
from core.config import settings
from core.llm import (
    ModelRole,
    UserAi,
    api_key_for,
    is_demo_for,
    mask_key,
    reset_user_ai,
    set_user_ai,
    user_ai_from_settings,
)

API = "/api/v1"


@pytest.fixture
def fernet(monkeypatch):
    """Turn field encryption on for a test (conftest leaves FERNET_KEY empty)."""
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "fernet_key", key)
    crypto._fernet.cache_clear()
    yield key
    crypto._fernet.cache_clear()


@pytest.fixture
def no_server_keys(monkeypatch):
    for name in ("anthropic_api_key", "openai_api_key", "google_api_key", "groq_api_key"):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "environment", "development")


@pytest.fixture
def clean_context():
    token = set_user_ai(None)
    yield
    reset_user_ai(token)


# --- Unit ------------------------------------------------------------------


def test_user_ai_from_settings_decrypts_and_ignores_junk(fernet):
    stored = {
        "ai": {
            "mode": "own",
            "keys": {
                "anthropic": crypto.encrypt("sk-ant-test-1234abcd"),
                "openai": "",
                "not-a-provider": crypto.encrypt("x"),
            },
        }
    }
    ai = user_ai_from_settings(stored)
    assert ai.mode == "own"
    assert ai.keys == {"anthropic": "sk-ant-test-1234abcd"}

    assert user_ai_from_settings(None) == UserAi()
    assert user_ai_from_settings({"ai": {"mode": "weird"}}).mode == "kapital"


def test_mask_key_never_reveals_more_than_the_tail():
    assert mask_key("sk-ant-test-1234abcd") == "…abcd"
    assert mask_key("short") == "…"


def test_key_resolution_and_demo_follow_the_mode(no_server_keys, clean_context, monkeypatch):
    # No server keys, no user context → dev auto-demo (legacy behaviour).
    assert api_key_for("anthropic") is None
    assert is_demo_for(ModelRole.PARSER) is True

    # Server key present, kapital mode → real model.
    monkeypatch.setattr(settings, "anthropic_api_key", "server-key")
    assert api_key_for("anthropic") == "server-key"
    assert is_demo_for(ModelRole.PARSER) is False

    # Own mode without a key: stub — the server key is NOT used behind the user's back.
    t = set_user_ai(UserAi(mode="own", keys={}))
    assert api_key_for("anthropic") is None
    assert is_demo_for(ModelRole.PARSER) is True
    reset_user_ai(t)

    # Own mode with a key: the user's key wins.
    t = set_user_ai(UserAi(mode="own", keys={"anthropic": "user-key"}))
    assert api_key_for("anthropic") == "user-key"
    assert is_demo_for(ModelRole.PARSER) is False
    reset_user_ai(t)

    # Explicit DEMO_MODE wins everywhere.
    monkeypatch.setattr(settings, "demo_mode", True)
    t = set_user_ai(UserAi(mode="own", keys={"anthropic": "user-key"}))
    assert is_demo_for(ModelRole.PARSER) is True
    reset_user_ai(t)


def test_get_model_passes_the_users_key_to_the_provider(no_server_keys, clean_context, monkeypatch):
    seen: list[dict] = []

    def fake_init(**kwargs):
        seen.append(kwargs)
        return object()

    monkeypatch.setattr(llm, "init_chat_model", fake_init)
    llm._build_model.cache_clear()
    monkeypatch.setattr(settings, "llm_parser", "anthropic:claude-sonnet-4-6")

    t = set_user_ai(UserAi(mode="own", keys={"anthropic": "user-key"}))
    first = llm.get_model(ModelRole.PARSER)
    again = llm.get_model(ModelRole.PARSER)
    reset_user_ai(t)
    t = set_user_ai(UserAi(mode="own", keys={"anthropic": "other-user-key"}))
    other = llm.get_model(ModelRole.PARSER)
    reset_user_ai(t)

    assert seen[0]["api_key"] == "user-key"
    assert seen[0]["model"] == "claude-sonnet-4-6" and seen[0]["model_provider"] == "anthropic"
    assert first is again  # cached per key
    assert other is not first and seen[1]["api_key"] == "other-user-key"
    llm._build_model.cache_clear()


# --- HTTP ------------------------------------------------------------------


async def _client() -> AsyncClient:
    from main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _register(client: AsyncClient) -> dict[str, str]:
    email = f"{uuid.uuid4().hex}@example.com"
    res = await client.post(f"{API}/auth/register", json={"email": email, "password": "supersecret1"})
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['tokens']['access_token']}"}


async def test_ai_settings_roundtrip_stores_encrypted_and_returns_masked(db_ready, fernet, no_server_keys):
    from sqlalchemy import select

    from core.db import SessionLocal
    from models.user import User

    async with await _client() as client:
        h = await _register(client)

        res = await client.get(f"{API}/account/ai", headers=h)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["mode"] == "kapital"
        assert body["server_ai_available"] is False
        assert body["demo"] is True
        assert [p["provider"] for p in body["providers"]] == ["anthropic", "openai", "google_genai", "groq"]
        assert all(p["configured"] is False and p["hint"] is None for p in body["providers"])

        res = await client.put(
            f"{API}/account/ai",
            json={"mode": "own", "keys": {"anthropic": "sk-ant-test-1234abcd"}},
            headers=h,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["mode"] == "own"
        assert body["demo"] is False  # the user's own key makes the parser real
        anth = next(p for p in body["providers"] if p["provider"] == "anthropic")
        assert anth["configured"] is True and anth["hint"] == "…abcd"
        assert "sk-ant-test-1234abcd" not in res.text

        # At rest: encrypted, never plaintext.
        async with SessionLocal() as session:
            user = await session.scalar(select(User).order_by(User.created_at.desc()))
            raw = json.dumps(user.settings)
        assert "sk-ant-test-1234abcd" not in raw
        assert user.settings["ai"]["mode"] == "own"
        assert crypto.decrypt(user.settings["ai"]["keys"]["anthropic"]) == "sk-ant-test-1234abcd"

        # Own mode but the key removed → back to a stub for this user.
        res = await client.put(f"{API}/account/ai", json={"keys": {"anthropic": None}}, headers=h)
        assert res.status_code == 200
        body = res.json()
        assert body["mode"] == "own"
        assert body["demo"] is True
        assert next(p for p in body["providers"] if p["provider"] == "anthropic")["configured"] is False

        # Validation.
        res = await client.put(f"{API}/account/ai", json={"mode": "free-lunch"}, headers=h)
        assert res.status_code == 422
        res = await client.put(f"{API}/account/ai", json={"keys": {"skynet": "k"}}, headers=h)
        assert res.status_code == 422


async def test_transcriber_uses_the_users_openai_key(no_server_keys, clean_context, monkeypatch):
    import core.transcription as tr

    monkeypatch.setattr(settings, "stt_provider", "openai")
    assert isinstance(tr.get_transcriber(), tr.DemoTranscriber)

    t = set_user_ai(UserAi(mode="own", keys={"openai": "user-openai-key"}))
    transcriber = tr.get_transcriber()
    reset_user_ai(t)
    assert isinstance(transcriber, tr.OpenAIWhisperTranscriber)
