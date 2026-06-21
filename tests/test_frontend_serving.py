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
async def test_security_headers_set_on_responses():
    """Defense-in-depth headers must be set on every response."""
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret")
    app = create_app(settings=settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
        csp = resp.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp
        # script-src must NOT contain 'unsafe-inline' — inline scripts in
        # the served index.html are now allow-listed by SHA-256 hash.
        # When the test runs without a static_dir, there's no index.html
        # and script-src is just 'self' — also no unsafe-inline.
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
        # Leaflet tile CDN must be allowed under img-src or the check-in
        # map renders blank — see CheckInMap.tsx's TILE_URL.
        img_src = csp.split("img-src")[1].split(";")[0]
        assert "basemaps.cartocdn.com" in img_src
        # HSTS only when https — default app_base_url is http://localhost:8000.
        assert "strict-transport-security" not in resp.headers


@pytest.mark.asyncio
async def test_csp_report_endpoint_accepts_violation_report():
    """The CSP header points at /api/csp-report; the endpoint must accept
    the standard `application/csp-report` envelope and respond 204 so the
    browser doesn't retry. Without an endpoint, CSP violations are silent."""
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret")
    app = create_app(settings=settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Headers point at the endpoint.
        resp = await client.get("/api/health")
        assert "/api/csp-report" in resp.headers.get("content-security-policy", "")
        assert "csp-endpoint" in resp.headers.get("reporting-endpoints", "")

        # Browser-shaped violation report → 204.
        report = {
            "csp-report": {
                "document-uri": "https://example.com/",
                "violated-directive": "script-src 'self'",
                "blocked-uri": "inline",
            }
        }
        violation = await client.post(
            "/api/csp-report",
            json=report,
            headers={"Content-Type": "application/csp-report"},
        )
        assert violation.status_code == 204


@pytest.mark.asyncio
async def test_csp_emits_sha256_for_inline_index_scripts(tmp_path):
    """When index.html contains an inline <script>, its SHA-256 is in CSP."""
    index = tmp_path / "index.html"
    # Realistic theme-bootstrap snippet (same shape as frontend/index.html).
    index.write_text(
        '<!doctype html><html><head><script>(function(){var t="dark";})();</script>'
        '</head><body></body></html>'
    )
    settings = Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        static_dir=str(tmp_path),
    )
    app = create_app(settings=settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
        csp = resp.headers.get("content-security-policy", "")
        # Compute the expected hash for the inline script body.
        import base64, hashlib
        expected = "'sha256-" + base64.b64encode(
            hashlib.sha256(b'(function(){var t="dark";})();').digest()
        ).decode("ascii") + "'"
        assert expected in csp
        # And the offending 'unsafe-inline' is absent from script-src.
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]


@pytest.mark.asyncio
async def test_hsts_is_set_when_https():
    settings = Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        app_base_url="https://example.com",
    )
    app = create_app(settings=settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://example.com") as client:
        resp = await client.get("/api/health")
        hsts = resp.headers.get("strict-transport-security", "")
        assert "max-age=" in hsts
        assert "includeSubDomains" in hsts


@pytest.mark.asyncio
async def test_trusted_host_rejects_mismatched_host():
    """When app_base_url is non-localhost, requests with a different Host
    header are refused before reaching any handler. Prevents proxy-Host-
    confusion attacks against code that consults request.url.hostname."""
    settings = Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        app_base_url="https://skynet.example.com",
    )
    app = create_app(settings=settings)
    transport = ASGITransport(app=app)
    # Spoofed Host header — should be rejected by TrustedHostMiddleware.
    async with AsyncClient(transport=transport, base_url="http://evil.example.com") as client:
        resp = await client.get("/api/health")
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_url_encoded_path_traversal_is_blocked():
    """Pre-auth file-read guard.

    Starlette collapses literal "../" but not URL-encoded "%2e%2e".
    Without the realpath/commonpath guard in serve_frontend, GET
    /%2e%2e/<file> resolves outside static_dir and `FileResponse` would
    serve arbitrary files readable by the server process.
    """
    with tempfile.TemporaryDirectory() as static_dir:
        with open(os.path.join(static_dir, "index.html"), "w") as f:
            f.write("<html><body>SkyNetControl</body></html>")

        # Place a "secret" file OUTSIDE the static dir but still in the
        # parent's reach via a relative traversal.
        secret_dir = os.path.dirname(static_dir)
        secret_path = os.path.join(secret_dir, "outside_secret.txt")
        with open(secret_path, "w") as f:
            f.write("SECRET-DO-NOT-SERVE")

        try:
            settings = Settings(database_url="sqlite:///", static_dir=static_dir, jwt_secret_key="test-secret")
            app = create_app(settings=settings)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # URL-encoded `../` survives Starlette's collapse.
                resp = await client.get("/%2e%2e/outside_secret.txt")
                # Must NOT serve the secret. The guard falls back to index.html.
                assert resp.status_code == 200
                assert "SECRET-DO-NOT-SERVE" not in resp.text
                assert "SkyNetControl" in resp.text
        finally:
            os.unlink(secret_path)


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
