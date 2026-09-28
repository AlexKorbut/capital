"""Personal API tokens — machine access for hosts such as Jarvis.

Covers: mint (plaintext shown once), list (never the secret), use as a Bearer
credential on real endpoints, per-user isolation, revoke → 401, garbage → 401,
"log out everywhere" leaves API tokens alive, last_used_at bookkeeping.
"""
from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

from core.tokens import TOKEN_PREFIX, VISIBLE_PREFIX_LEN, generate_token, hash_token, visible_prefix

API = "/api/v1"


# --- Pure helpers ---------------------------------------------------------


def test_generate_token_shape_and_hash_are_stable():
    plain = generate_token()
    assert plain.startswith(TOKEN_PREFIX)
    assert len(plain) > 40
    assert visible_prefix(plain) == plain[:VISIBLE_PREFIX_LEN]
    assert hash_token(plain) == hash_token(plain)
    assert hash_token(plain) != hash_token(generate_token())
    assert len(hash_token(plain)) == 64


# --- HTTP fixtures --------------------------------------------------------


async def _client() -> AsyncClient:
    from main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _register(client: AsyncClient) -> dict:
    email = f"{uuid.uuid4().hex}@example.com"
    res = await client.post(
        f"{API}/auth/register", json={"email": email, "password": "supersecret1"}
    )
    assert res.status_code == 201, res.text
    data = res.json()
    data["email"] = email
    return data


def _jwt(auth: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth['tokens']['access_token']}"}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Behaviour ------------------------------------------------------------


async def test_mint_list_use_and_revoke(db_ready):
    async with await _client() as client:
        auth = await _register(client)

        # Mint: plaintext comes back exactly once.
        res = await client.post(f"{API}/account/tokens", json={"name": "jarvis"}, headers=_jwt(auth))
        assert res.status_code == 201, res.text
        created = res.json()
        token = created["token"]
        assert token.startswith(TOKEN_PREFIX)
        assert created["name"] == "jarvis"
        assert created["prefix"] == token[:VISIBLE_PREFIX_LEN]
        assert created["last_used_at"] is None

        # List: metadata only, never the secret.
        res = await client.get(f"{API}/account/tokens", headers=_jwt(auth))
        assert res.status_code == 200
        rows = res.json()
        assert len(rows) == 1
        assert rows[0]["id"] == created["id"]
        assert rows[0]["prefix"] == created["prefix"]
        assert "token" not in rows[0]
        assert token not in res.text

        # Use: the token authenticates real endpoints as that user.
        res = await client.get(f"{API}/auth/me", headers=_bearer(token))
        assert res.status_code == 200, res.text
        assert res.json()["email"] == auth["email"]
        res = await client.get(f"{API}/portfolio/current", headers=_bearer(token))
        assert res.status_code == 200, res.text

        # Bookkeeping: last_used_at is stamped after use.
        res = await client.get(f"{API}/account/tokens", headers=_jwt(auth))
        assert res.json()[0]["last_used_at"] is not None

        # Revoke: 204, then the token is dead and gone from the list.
        res = await client.delete(f"{API}/account/tokens/{created['id']}", headers=_jwt(auth))
        assert res.status_code == 204
        res = await client.get(f"{API}/auth/me", headers=_bearer(token))
        assert res.status_code == 401
        res = await client.get(f"{API}/account/tokens", headers=_jwt(auth))
        assert res.json() == []
        # Revoking twice is a 404, not a silent success.
        res = await client.delete(f"{API}/account/tokens/{created['id']}", headers=_jwt(auth))
        assert res.status_code == 404


async def test_tokens_are_isolated_between_users(db_ready):
    async with await _client() as client:
        alice = await _register(client)
        bob = await _register(client)
        res = await client.post(f"{API}/account/tokens", json={"name": "a"}, headers=_jwt(alice))
        token_id = res.json()["id"]

        res = await client.get(f"{API}/account/tokens", headers=_jwt(bob))
        assert res.json() == []
        res = await client.delete(f"{API}/account/tokens/{token_id}", headers=_jwt(bob))
        assert res.status_code == 404
        # Alice's token is untouched.
        res = await client.get(f"{API}/account/tokens", headers=_jwt(alice))
        assert len(res.json()) == 1


async def test_unknown_and_malformed_tokens_are_rejected(db_ready):
    async with await _client() as client:
        res = await client.get(f"{API}/auth/me", headers=_bearer(TOKEN_PREFIX + "definitely-not-issued"))
        assert res.status_code == 401
        res = await client.get(f"{API}/auth/me", headers=_bearer("not-a-jwt-not-a-token"))
        assert res.status_code == 401


async def test_logout_everywhere_keeps_api_tokens_alive(db_ready):
    async with await _client() as client:
        auth = await _register(client)
        res = await client.post(f"{API}/account/tokens", json={"name": "jarvis"}, headers=_jwt(auth))
        token = res.json()["token"]

        res = await client.post(f"{API}/auth/logout-all", headers=_jwt(auth))
        assert res.status_code == 200, res.text
        # Old JWT is dead...
        res = await client.get(f"{API}/auth/me", headers=_jwt(auth))
        assert res.status_code == 401
        # ...the API token still works: it is revoked explicitly, not by session bumps.
        res = await client.get(f"{API}/auth/me", headers=_bearer(token))
        assert res.status_code == 200


async def test_token_name_is_required(db_ready):
    async with await _client() as client:
        auth = await _register(client)
        res = await client.post(f"{API}/account/tokens", json={}, headers=_jwt(auth))
        assert res.status_code == 422
        res = await client.post(f"{API}/account/tokens", json={"name": ""}, headers=_jwt(auth))
        assert res.status_code == 422


async def test_deleting_the_account_removes_its_tokens(db_ready):
    from sqlalchemy import func, select

    from core.db import SessionLocal
    from models.api_token import ApiToken

    async with await _client() as client:
        auth = await _register(client)
        res = await client.post(f"{API}/account/tokens", json={"name": "jarvis"}, headers=_jwt(auth))
        token = res.json()["token"]
        res = await client.request(
            "DELETE",
            f"{API}/account",
            json={"confirm_email": auth["email"]},
            headers=_jwt(auth),
        )
        assert res.status_code == 204, res.text
        res = await client.get(f"{API}/auth/me", headers=_bearer(token))
        assert res.status_code == 401

    async with SessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(ApiToken))
    assert count == 0
