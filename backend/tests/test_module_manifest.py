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


async def test_manifest_announces_finance_projects_next_to_the_old_capabilities():
    from main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        caps = (await client.get(f"{API}/module")).json()["capabilities"]

    # New capabilities are added; nothing the host already relies on goes away.
    for old in ("portfolio.read", "portfolio.write", "goals", "advice", "scenarios", "wallets"):
        assert old in caps
    for new in ("finance.projects", "finance.expenses", "finance.reports"):
        assert new in caps
    assert len(caps) == len(set(caps))


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
        for path in (
            "/projects",
            "/projects/{project_id}",
            "/projects/{project_id}/expenses",
            "/projects/{project_id}/report",
            "/projects/{project_id}/revalue",
            "/expenses/{expense_id}",
            "/fx",
            "/categories",
        ):
            assert f"{API}/finance{path}" in paths, path


async def test_manifest_announces_finance_attachments_and_their_limits():
    from main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        m = (await client.get(f"{API}/module")).json()

    assert "finance.attachments" in m["capabilities"]
    for old in ("portfolio.read", "goals", "finance.projects", "finance.expenses", "finance.reports"):
        assert old in m["capabilities"]  # capabilities only grow
    assert len(m["capabilities"]) == len(set(m["capabilities"]))
    assert m["limits"] == {
        "attachment_max_bytes": 10 * 1024 * 1024,
        "attachment_mime": ["image/jpeg", "image/png", "image/webp"],
    }


async def test_openapi_lists_the_attachment_routes_and_the_multipart_upload():
    from main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        paths = (await client.get("/openapi.json")).json()["paths"]

    upload = paths[f"{API}/finance/expenses/{{expense_id}}/attachments"]["post"]
    assert "multipart/form-data" in upload["requestBody"]["content"]
    assert set(paths[f"{API}/finance/attachments/{{attachment_id}}"]) == {"get", "delete"}
