import pytest


@pytest.mark.asyncio
async def test_health_includes_database_status(client):
    response = await client.get("/api/health")
    data = response.json()
    assert data["database"] == "connected"
