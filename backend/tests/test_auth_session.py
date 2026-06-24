"""Session security: refresh-token cookie, TOTP single-use, WS auth resolver."""
from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

API = "/api/v1"


async def _client() -> AsyncClient:
    from main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _register(client: AsyncClient) -> tuple[dict, str]:
    email = f"{uuid.uuid4().hex}@example.com"
    res = await client.post(
        f"{API}/auth/register", json={"email": email, "password": "supersecret1"}
    )
    assert res.status_code == 201, res.text
    return res.json(), email


# --- Refresh-token cookie -------------------------------------------------


async def test_refresh_token_only_in_httponly_cookie(db_ready):
    async with await _client() as client:
        reg, _ = await _register(client)
        # Never exposed in the JS-readable body...
        assert reg["tokens"]["refresh_token"] == ""
        # ...only as an httpOnly cookie.
        assert "kapital_refresh" in client.cookies


async def test_refresh_uses_cookie_not_body(db_ready):
    async with await _client() as client:
        await _register(client)  # sets the cookie on the client jar
        res = await client.post(f"{API}/auth/refresh", json={})
        assert res.status_code == 200, res.text
        assert res.json()["access_token"]
        assert res.json()["refresh_token"] == ""


async def test_refresh_without_cookie_is_401(db_ready):
    async with await _client() as client:  # fresh jar, never registered
        res = await client.post(f"{API}/auth/refresh", json={})
        assert res.status_code == 401


async def test_refresh_accepts_body_token_for_non_browser_clients(db_ready):
    """The body-token fallback (no cookie) still works for non-browser clients."""
    from core.security import create_refresh_token

    async with await _client() as client:
        reg, _ = await _register(client)
        client.cookies.clear()  # no cookie → must use the body token
        tok = create_refresh_token(reg["user"]["id"], 0)
        res = await client.post(f"{API}/auth/refresh", json={"refresh_token": tok})
        assert res.status_code == 200, res.text
        assert res.json()["access_token"]


async def test_logout_clears_cookie(db_ready):
    async with await _client() as client:
        await _register(client)
        out = await client.post(f"{API}/auth/logout", json={})
        assert out.status_code == 200
        # Cookie removed from the jar ⇒ refresh now fails.
        assert "kapital_refresh" not in client.cookies
        res = await client.post(f"{API}/auth/refresh", json={})
        assert res.status_code == 401


# --- TOTP single-use (replay protection) ----------------------------------


async def test_totp_code_is_single_use(db_ready, monkeypatch):
    import core.totp as totp

    # Freeze time so codes are deterministic and not flaky on a 30s boundary.
    monkeypatch.setattr(totp.time, "time", lambda: 1_700_000_000.0)
    counter = int(1_700_000_000.0 // totp._PERIOD)

    async with await _client() as client:
        reg, email = await _register(client)
        auth = {"Authorization": f"Bearer {reg['tokens']['access_token']}"}
        setup = await client.post(f"{API}/auth/2fa/setup", json={}, headers=auth)
        secret = setup.json()["secret"]

        code = totp._hotp(secret, counter)
        en = await client.post(f"{API}/auth/2fa/enable", json={"code": code}, headers=auth)
        assert en.status_code == 200, en.text

        # The code used to enable is now burned — reusing it at login is rejected.
        reused = await client.post(
            f"{API}/auth/login",
            json={"email": email, "password": "supersecret1", "code": code},
        )
        assert reused.status_code == 401

        # A fresh (later-step) code still works.
        fresh = totp._hotp(secret, counter + 1)
        ok = await client.post(
            f"{API}/auth/login",
            json={"email": email, "password": "supersecret1", "code": fresh},
        )
        assert ok.status_code == 200, ok.text


# --- WebSocket auth resolver ----------------------------------------------


async def test_ws_user_from_token(db_ready):
    from core.security import create_access_token
    from routers.advice import _user_from_token

    async with await _client() as client:
        reg, _ = await _register(client)
    uid = reg["user"]["id"]

    user = await _user_from_token(create_access_token(uid, 0))
    assert user is not None and str(user.id) == uid
    # Missing / malformed tokens never resolve to a user (handshake closes 1008).
    assert await _user_from_token(None) is None
    assert await _user_from_token("not-a-jwt") is None
