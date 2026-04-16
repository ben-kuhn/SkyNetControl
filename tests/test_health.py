import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_health_includes_version(client):
    response = await client.get("/api/health")
    data = response.json()
    assert "version" in data
    assert data["version"] == "0.1.0"
