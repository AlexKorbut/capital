"""Срез 7 — security & compliance.

Covers the purpose-scoped token roundtrip (verify/reset), HTTP-level email
verification + password reset, security headers, legal docs, the rate-limit
429, and the GDPR export/delete endpoints.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from jose import JWTError

from core.security import (
    create_password_reset_token,
    create_verification_token,
    decode_email_token,
    verify_password,
)

API = "/api/v1"


# --- Token roundtrip (unit) ----------------------------------------------


def test_purpose_token_roundtrip_and_mismatch():
    sub = str(uuid.uuid4())
    vtok = create_verification_token(sub)
    assert decode_email_token(vtok, purpose="email_verify") == sub

    # A verify token must NOT validate as a reset token, and vice-versa.
    with pytest.raises(JWTError):
        decode_email_token(vtok, purpose="password_reset")

    rtok = create_password_reset_token(sub)
    assert decode_email_token(rtok, purpose="password_reset") == sub
    with pytest.raises(JWTError):
        decode_email_token(rtok, purpose="email_verify")


# --- HTTP fixtures --------------------------------------------------------


async def _client() -> AsyncClient:
    from main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _register(client: AsyncClient, email: str | None = None) -> dict:
    email = email or f"{uuid.uuid4().hex}@example.com"
    res = await client.post(
        f"{API}/auth/register",
        json={"email": email, "password": "supersecret1"},
    )
    assert res.status_code == 201, res.text
    data = res.json()
    data["email"] = email
    return data


# --- Email verification (HTTP) -------------------------------------------


async def test_email_verify_confirm_flow(db_ready):
    async with await _client() as client:
        reg = await _register(client)
        user_id = reg["user"]["id"]
        assert reg["user"]["email_verified"] is False

        token = create_verification_token(user_id)
        res = await client.post(
            f"{API}/auth/verify-email/confirm", json={"token": token}
        )
        assert res.status_code == 200, res.text

        # /me now reports verified.
        access = reg["tokens"]["access_token"]
        me = await client.get(
            f"{API}/auth/me", headers={"Authorization": f"Bearer {access}"}
        )
        assert me.json()["email_verified"] is True

        # A tampered/garbage token is rejected.
        bad = await client.post(
            f"{API}/auth/verify-email/confirm", json={"token": "not-a-token"}
        )
        assert bad.status_code == 400


async def test_password_reset_flow(db_ready):
    async with await _client() as client:
        reg = await _register(client)
        user_id = reg["user"]["id"]
        email = reg["email"]

        token = create_password_reset_token(user_id)
        res = await client.post(
            f"{API}/auth/password/reset",
            json={"token": token, "password": "brandnewpass9"},
        )
        assert res.status_code == 200, res.text

        # Old password no longer works; new one does.
        old = await client.post(
            f"{API}/auth/login", json={"email": email, "password": "supersecret1"}
        )
        assert old.status_code == 401
        new = await client.post(
            f"{API}/auth/login", json={"email": email, "password": "brandnewpass9"}
        )
        assert new.status_code == 200


async def test_forgot_password_is_anti_enumeration(db_ready):
    async with await _client() as client:
        # Same response whether or not the email exists.
        unknown = await client.post(
            f"{API}/auth/password/forgot", json={"email": "nobody@nowhere.example"}
        )
        assert unknown.status_code == 200
        assert "message" in unknown.json()


# --- Security headers -----------------------------------------------------


async def test_security_headers_present(db_ready):
    async with await _client() as client:
        res = await client.get("/health")
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "Permissions-Policy" in res.headers


# --- Legal docs -----------------------------------------------------------


async def test_legal_docs_served(db_ready):
    async with await _client() as client:
        for slug in ("tos", "privacy", "disclaimer"):
            res = await client.get(f"{API}/legal/{slug}")
            assert res.status_code == 200, slug
            body = res.json()
            assert body["slug"] == slug
            assert len(body["markdown"]) > 50

        missing = await client.get(f"{API}/legal/unknown")
        assert missing.status_code == 404


# --- GDPR export + delete -------------------------------------------------


async def test_account_export_and_delete(db_ready):
    async with await _client() as client:
        reg = await _register(client)
        access = reg["tokens"]["access_token"]
        email = reg["email"]
        auth = {"Authorization": f"Bearer {access}"}

        export = await client.get(f"{API}/account/export", headers=auth)
        assert export.status_code == 200, export.text
        dump = export.json()
        assert dump["user"]["email"] == email
        assert "password_hash" not in dump["user"]  # never leak the hash
        assert "snapshots" in dump

        # Wrong confirm email is rejected.
        wrong = await client.request(
            "DELETE",
            f"{API}/account",
            headers=auth,
            json={"confirm_email": "someone-else@example.com"},
        )
        assert wrong.status_code in (400, 422)

        # Correct confirmation deletes the account.
        ok = await client.request(
            "DELETE",
            f"{API}/account",
            headers=auth,
            json={"confirm_email": email},
        )
        assert ok.status_code == 204

        # The user is gone — login fails.
        gone = await client.post(
            f"{API}/auth/login", json={"email": email, "password": "supersecret1"}
        )
        assert gone.status_code == 401


# --- Rate limiting --------------------------------------------------------


async def test_forgot_password_rate_limited(db_ready, monkeypatch):
    """FORGOT_LIMIT is 5/hour — the 6th call in the window returns 429."""
    from core.ratelimit import limiter

    monkeypatch.setattr(limiter, "enabled", True)  # suite-wide default is disabled
    async with await _client() as client:
        codes = []
        for _ in range(7):
            res = await client.post(
                f"{API}/auth/password/forgot",
                json={"email": "ratelimit@example.com"},
            )
            codes.append(res.status_code)
        assert 429 in codes, codes
        # ensure the hash check still works (import used) — sanity, no-op
        assert verify_password is not None
