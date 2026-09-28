"""Module manifest — how a host (Jarvis) discovers and talks to KAPITAL."""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

API = "/api/v1"


async def test_module_manifest_is_public_and_complete():
    from main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        res = await client.get(f"{API}/module")
        assert res.status_code == 200, res.text
        m = res.json()

    assert m["name"] == "kapital"
    assert m["title"] == "КАПИТАЛЬ"
    assert isinstance(m["version"], str) and m["version"]
    assert m["api_prefix"] == API
    assert m["auth"] == {"scheme": "bearer", "token_prefix": "kp_", "setup_path": "/settings"}
    assert m["health"] == f"{API}/health"
    assert m["openapi"] == "/openapi.json"
    assert "portfolio.read" in m["capabilities"] and "portfolio.write" in m["capabilities"]


async def test_health_and_openapi_named_by_the_manifest_exist():
    from main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        m = (await client.get(f"{API}/module")).json()
        health = await client.get(m["health"])
        assert health.status_code == 200 and health.json()["status"] == "ok"
        openapi = await client.get(m["openapi"])
        assert openapi.status_code == 200
        paths = openapi.json()["paths"]
        assert f"{API}/account/tokens" in paths and f"{API}/account/ai" in paths
