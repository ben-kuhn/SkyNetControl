"""Tests for the first-boot setup wizard backend.

Setup-completion uses the unified `/api/auth/callback/{slug}` URI dispatched
on a state lookup in `_SETUP_SESSIONS`. There is no longer a separate
`/api/setup/claim/callback` route — the wizard's Step 4 OAuth redirect
lands on the same endpoint as everyday sign-in.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.routes import auth_router
from backend.config import Settings
from backend.config_mgmt.setup_routes import _SETUP_SESSIONS
from backend.config_mgmt.setup_state import mark_setup_completed
from backend.db.base import Base


@pytest.fixture(autouse=True)
def clear_setup_sessions():
    """Clear in-memory session store before every test."""
    _SETUP_SESSIONS.clear()
    yield
    _SETUP_SESSIONS.clear()


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
        app_base_url="http://testserver",
    )


@pytest.fixture
def db_setup():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory


@pytest.fixture
def test_app(test_settings, db_setup):
    from backend.config_mgmt.setup_routes import setup_router

    engine, factory = db_setup
    app = FastAPI()
    app.state.session_factory = factory
    app.state.engine = engine
    app.state.settings = test_settings
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(setup_router, prefix="/api/setup")
    return app


# Shared wizard input body (github is oauth2 — no discovery needed)
_WIZARD_BODY = {
    "default_net_control": "W0NE",
    "net_address": "w0ne@winlink.org",
    "app_base_url": "http://testserver",
    "oauth_slug": "github",
    "oauth_name": "GitHub",
    "oauth_client_id": "my-client-id",
    "oauth_client_secret": "my-client-secret",
    "oauth_issuer_url": "",
    "smtp": None,
}


def _make_mock_http_client(token_resp_data: dict, userinfo_resp_data: dict | None = None):
    """Build a mock httpx.AsyncClient for token + optional userinfo calls."""
    mock_token_resp = MagicMock(spec=Response)
    mock_token_resp.json.return_value = token_resp_data

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_token_resp)

    if userinfo_resp_data is not None:
        mock_userinfo_resp = MagicMock(spec=Response)
        mock_userinfo_resp.json.return_value = userinfo_resp_data
        mock_client.get = AsyncMock(return_value=mock_userinfo_resp)

    return mock_client


# ---------------------------------------------------------------------------
# Test 1: status returns false when unset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_returns_false_when_unset(test_app):
    """GET /api/setup/status with empty DB → {setup_completed: false}."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/setup/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["setup_completed"] is False
    assert body["recovery_mode"] is False


# ---------------------------------------------------------------------------
# Test 2: status returns true after completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_returns_true_after_completion(test_app):
    """Pre-populate setup_completed → GET /status returns true."""
    with test_app.state.session_factory() as db:
        mark_setup_completed(db)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/setup/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["setup_completed"] is True
    assert body["recovery_mode"] is False


# ---------------------------------------------------------------------------
# Test 3: claim/start returns authorize_url with state and client_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_start_returns_authorize_url_with_state(test_app):
    """POST /api/setup/claim/start → 200, authorize_url contains state + client_id."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/setup/claim/start", json=_WIZARD_BODY)
    assert resp.status_code == 200
    body = resp.json()
    assert "authorize_url" in body
    url = body["authorize_url"]
    assert "state=" in url
    assert "my-client-id" in url
    assert "github.com" in url
    # The unified redirect URI matches everyday sign-in — operators register
    # exactly one URI at the IdP. Old /api/setup/claim/callback is gone.
    assert "redirect_uri=http%3A%2F%2Ftestserver%2Fapi%2Fauth%2Fcallback%2Fgithub" in url


# ---------------------------------------------------------------------------
# Test 4: claim/start 410 after setup complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_start_410_after_setup_complete(test_app):
    """POST claim/start after setup is complete → 410."""
    with test_app.state.session_factory() as db:
        mark_setup_completed(db)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/setup/claim/start", json=_WIZARD_BODY)
    assert resp.status_code == 410


# ---------------------------------------------------------------------------
# Test 5: claim/start rejects invalid slug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_start_rejects_invalid_slug(test_app):
    """POST with oauth_slug='Bad Slug!' → 400."""
    body = {**_WIZARD_BODY, "oauth_slug": "Bad Slug!"}
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/setup/claim/start", json=body)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Test 6: claim/start rejects blank client_secret
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_start_rejects_blank_secret(test_app):
    """POST with oauth_client_secret='' → 400 (no preserve semantics in wizard)."""
    body = {**_WIZARD_BODY, "oauth_client_secret": ""}
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/setup/claim/start", json=body)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Test 7: unified callback happy path — admin user, appconfig, JWT cookie
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(reason="role attribute removed in Task 3; restored as is_admin/is_pending/is_deleted in Task 4", strict=False)
async def test_unified_callback_creates_admin_and_marks_complete(test_app):
    """Full happy path: start → /api/auth/callback/{slug} (mocked) → User + AppConfig rows + JWT cookie."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        # 1) Start
        start_resp = await client.post("/api/setup/claim/start", json=_WIZARD_BODY)
        assert start_resp.status_code == 200
        state = next(iter(_SETUP_SESSIONS))

        # 2) Callback hits the unified everyday-sign-in URI; setup-dispatch
        # is via the state lookup, not a separate route.
        mock_client = _make_mock_http_client(
            token_resp_data={"access_token": "tok123"},
            userinfo_resp_data={"id": 42, "login": "testuser", "name": "Test User", "email": "test@example.com"},
        )
        with patch("backend.config_mgmt.setup_routes.httpx.AsyncClient", return_value=mock_client):
            cb_resp = await client.get(
                "/api/auth/callback/github",
                params={"state": state, "code": "auth-code-xyz"},
            )

    # Must redirect to /
    assert cb_resp.status_code == 302
    assert cb_resp.headers["location"] == "/"

    # JWT cookie must be set
    assert "access_token" in cb_resp.cookies

    # Verify DB state
    from backend.auth.models import User
    from backend.config_mgmt.models import AppConfig
    from backend.config_mgmt.setup_state import is_setup_completed

    with test_app.state.session_factory() as db:
        assert is_setup_completed(db)

        user = db.get(User, "W0NE")
        assert user is not None
        assert user.role.value == "admin"
        assert user.oidc_subject == "github:42"

        assert db.get(AppConfig, "default_net_control") is not None
        assert db.get(AppConfig, "net_address") is not None
        assert db.get(AppConfig, "app_base_url") is not None
        assert db.get(AppConfig, "oauth.github.client_id") is not None
        assert db.get(AppConfig, "setup_completed") is not None


# ---------------------------------------------------------------------------
# Test 8: unknown state on the unified callback falls through to normal sign-in
# (which rejects with 400 — no oauth_state cookie, no provider in DB).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unified_callback_unknown_state_rejects(test_app):
    """No setup session + no oauth_state cookie → normal-flow rejection."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/auth/callback/github",
            params={"state": "no-such-state", "code": "x"},
        )
    # Normal flow rejects: either bad state (400) or unknown provider (404
    # if it gets that far). Either way it's not a 2xx.
    assert resp.status_code in (400, 404)


# ---------------------------------------------------------------------------
# Test 9: token exchange failure does not commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_token_exchange_failure_does_not_commit(test_app):
    """When token exchange returns no access_token, nothing is committed to the DB."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_resp = await client.post("/api/setup/claim/start", json=_WIZARD_BODY)
        assert start_resp.status_code == 200
        state = next(iter(_SETUP_SESSIONS))

        mock_client = _make_mock_http_client(
            token_resp_data={"error": "invalid_client", "error_description": "bad credentials"},
        )
        with patch("backend.config_mgmt.setup_routes.httpx.AsyncClient", return_value=mock_client):
            cb_resp = await client.get(
                "/api/auth/callback/github",
                params={"state": state, "code": "bad-code"},
            )

    # Should get an error HTML page, not a redirect
    assert cb_resp.status_code == 400
    assert "Token Exchange Failed" in cb_resp.text or "access_token" in cb_resp.text.lower()

    # Nothing committed
    from backend.config_mgmt.setup_state import is_setup_completed
    from backend.auth.models import User

    with test_app.state.session_factory() as db:
        assert not is_setup_completed(db)
        assert db.query(User).count() == 0


# 10 — XSS hardening: provider-controlled `?error` param is HTML-escaped
@pytest.mark.asyncio
async def test_callback_escapes_provider_error_in_html(test_app):
    """A provider sending ?error=<script>... must not produce un-escaped HTML."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_resp = await client.post("/api/setup/claim/start", json=_WIZARD_BODY)
        assert start_resp.status_code == 200
        state = next(iter(_SETUP_SESSIONS))

        cb_resp = await client.get(
            "/api/auth/callback/github",
            params={"state": state, "error": "<script>alert(1)</script>"},
        )
    assert cb_resp.status_code == 400
    body = cb_resp.text
    # Raw script tag must not appear; the escaped form must.
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
