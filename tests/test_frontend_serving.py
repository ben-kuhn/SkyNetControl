import os
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import create_app
from backend.config import Settings


@pytest.mark.asyncio
async def test_serves_index_html_at_root():
    with tempfile.TemporaryDirectory() as static_dir:
        index_path = os.path.join(static_dir, "index.html")
        with open(index_path, "w") as f:
            f.write("<html><body>SkyNetControl</body></html>")

        settings = Settings(database_url="sqlite:///", static_dir=static_dir)
        app = create_app(settings=settings)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/")
            assert response.status_code == 200
            assert "SkyNetControl" in response.text


@pytest.mark.asyncio
async def test_api_routes_take_priority_over_static():
    with tempfile.TemporaryDirectory() as static_dir:
        index_path = os.path.join(static_dir, "index.html")
        with open(index_path, "w") as f:
            f.write("<html><body>SkyNetControl</body></html>")

        settings = Settings(database_url="sqlite:///", static_dir=static_dir)
        app = create_app(settings=settings)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
