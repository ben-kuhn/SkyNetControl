import os
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import create_app
from backend.config import Settings


def test_create_app_rejects_default_jwt_secret():
    """Audit M6: starting with the default JWT secret must fail fast."""
    settings = Settings(database_url="sqlite:///")  # default jwt_secret_key
    with pytest.raises(RuntimeError, match="SKYNET_JWT_SECRET_KEY"):
        create_app(settings=settings)


@pytest.mark.asyncio
async def test_serves_index_html_at_root():
    with tempfile.TemporaryDirectory() as static_dir:
        index_path = os.path.join(static_dir, "index.html")
        with open(index_path, "w") as f:
            f.write("<html><body>SkyNetControl</body></html>")

        settings = Settings(database_url="sqlite:///", static_dir=static_dir, jwt_secret_key="test-secret")
        app = create_app(settings=settings)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/")
            assert response.status_code == 200
            assert "SkyNetControl" in response.text


@pytest.mark.asyncio
async def test_index_html_is_no_cache_and_assets_are_immutable():
    """Stale-HTML-after-redeploy guard.

    Nix-store files have a 1970 mtime, so without explicit Cache-Control
    browsers apply heuristic freshness measured in years and hold onto
    stale index.html across redeploys — pointing at asset hashes the new
    build no longer ships. index.html must revalidate every load; the
    content-hashed assets can (and should) be cached forever.
    """
    with tempfile.TemporaryDirectory() as static_dir:
        with open(os.path.join(static_dir, "index.html"), "w") as f:
            f.write("<html><body>SkyNetControl</body></html>")
        assets_dir = os.path.join(static_dir, "assets")
        os.makedirs(assets_dir)
        with open(os.path.join(assets_dir, "index-abc123.css"), "w") as f:
            f.write("body{}")

        settings = Settings(database_url="sqlite:///", static_dir=static_dir, jwt_secret_key="test-secret")
        app = create_app(settings=settings)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            index_resp = await client.get("/")
            assert index_resp.status_code == 200
            assert index_resp.headers["cache-control"] == "no-cache"

            asset_resp = await client.get("/assets/index-abc123.css")
            assert asset_resp.status_code == 200
            assert asset_resp.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.asyncio
async def test_api_routes_take_priority_over_static():
    with tempfile.TemporaryDirectory() as static_dir:
        index_path = os.path.join(static_dir, "index.html")
        with open(index_path, "w") as f:
            f.write("<html><body>SkyNetControl</body></html>")

        settings = Settings(database_url="sqlite:///", static_dir=static_dir, jwt_secret_key="test-secret")
        app = create_app(settings=settings)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
