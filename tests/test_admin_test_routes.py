"""Tests for the OAuth round-trip + SMTP send test endpoints."""
import smtplib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.auth.routes import auth_router
from backend.config import Settings
from backend.config_mgmt.smtp import SmtpConfig, upsert_smtp_config
from backend.config_mgmt.test_routes import _TEST_SESSIONS
from backend.db.base import Base
from tests.conftest import make_test_token


@pytest.fixture(autouse=True)
def clear_test_sessions():
    """Clear in-memory session store before every test."""
    _TEST_SESSIONS.clear()
    yield
    _TEST_SESSIONS.clear()


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
    from backend.config_mgmt.test_routes import test_router

    engine, factory = db_setup
    app = FastAPI()
    app.state.session_factory = factory
    app.state.engine = engine
    app.state.settings = test_settings
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(test_router, prefix="/api/admin")
    return app


@pytest.fixture
async def admin_client(test_app, test_settings):
    factory = test_app.state.session_factory
    with factory() as db:
        admin = User(callsign="W0NE", oidc_subject="test:admin", name="Admin", is_admin=True)
        db.add(admin)
        db.commit()
        db.refresh(admin)
    token = make_test_token("W0NE", test_settings, is_admin=True)
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, token


# ---------------------------------------------------------------------------
# Test 1: start returns an authorize URL with state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_returns_authorize_url_with_state(admin_client):
    """POST /start should return test_session_id + authorize_url containing state."""
    client, token = admin_client

    # Patch discovery for GitHub (oauth2, no discovery) — just use github which is plain oauth2
    resp = await client.post(
        "/api/admin/test/oauth/github/start",
        json={"client_id": "my-cid", "client_secret": "my-sec", "issuer_url": "", "name": "GitHub"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "test_session_id" in body
    assert "authorize_url" in body
    url = body["authorize_url"]
    assert "state=" in url
    assert "my-cid" in url
    assert "github.com" in url


# Stored-secret fallback: empty client_secret + saved provider → use stored secret
@pytest.mark.asyncio
async def test_start_falls_back_to_stored_secret(admin_client, test_app):
    """When body.client_secret == "" and a provider is saved, use its stored secret."""
    from backend.config_mgmt.oauth import OAuthProviderConfig, upsert_oauth_provider
    from backend.config_mgmt.test_routes import _TEST_SESSIONS

    client, token = admin_client
    with test_app.state.session_factory() as db:
        upsert_oauth_provider(db, OAuthProviderConfig(
            slug="github", name="GitHub", enabled=True,
            client_id="cid", client_secret="STORED-SECRET", issuer_url="",
        ))

    resp = await client.post(
        "/api/admin/test/oauth/github/start",
        json={"client_id": "cid", "client_secret": "", "issuer_url": "", "name": "GitHub"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    state = next(iter(_TEST_SESSIONS))
    assert _TEST_SESSIONS[state].client_secret == "STORED-SECRET"


# Stored-secret fallback: empty client_secret + no saved provider → 400
@pytest.mark.asyncio
async def test_start_empty_secret_without_stored_returns_400(admin_client):
    """When body.client_secret == "" and nothing is saved, return 400 instead of silently using empty."""
    client, token = admin_client
    resp = await client.post(
        "/api/admin/test/oauth/github/start",
        json={"client_id": "cid", "client_secret": "", "issuer_url": "", "name": "GitHub"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 400
    assert "client_secret" in resp.text.lower()


# ---------------------------------------------------------------------------
# Test 2: callback with unknown state returns 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_with_unknown_state_falls_through(test_app):
    """Unified callback: unknown state means no live test session, so the test
    dispatcher returns None and the request falls through to normal sign-in,
    which 400s on the missing OAuth state cookie."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/auth/callback/github",
            params={"state": "nonexistent-state", "code": "someCode"},
        )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Test 3: successful callback marks session as success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_success_marks_session(admin_client, test_app):
    """Full happy path: start → callback (mocked) → result returns success."""
    client, token = admin_client

    # 1) Start — use github (oauth2, no discovery needed)
    start_resp = await client.post(
        "/api/admin/test/oauth/github/start",
        json={"client_id": "cid", "client_secret": "csec", "issuer_url": "", "name": "GitHub"},
        cookies={"access_token": token},
    )
    assert start_resp.status_code == 200
    test_session_id = start_resp.json()["test_session_id"]

    # Retrieve the state that was stored
    state = next(iter(_TEST_SESSIONS))

    # 2) Mock token + userinfo endpoints
    mock_token_resp = MagicMock(spec=Response)
    mock_token_resp.json.return_value = {"access_token": "tok123"}

    mock_userinfo_resp = MagicMock(spec=Response)
    mock_userinfo_resp.json.return_value = {"sub": "42", "name": "Test User", "email": "test@example.com", "extra": "ignored"}

    mock_http_client = AsyncMock()
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)
    mock_http_client.post = AsyncMock(return_value=mock_token_resp)
    mock_http_client.get = AsyncMock(return_value=mock_userinfo_resp)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as raw_client:
        with patch("backend.config_mgmt.test_routes.httpx.AsyncClient", return_value=mock_http_client):
            cb_resp = await raw_client.get(
                "/api/auth/callback/github",
                params={"state": state, "code": "auth-code-xyz"},
            )
    assert cb_resp.status_code == 200
    assert "oauth_test" in cb_resp.text
    assert "success" in cb_resp.text

    # 3) Check result endpoint
    result_resp = await client.get(
        f"/api/admin/test/oauth/{test_session_id}/result",
        cookies={"access_token": token},
    )
    assert result_resp.status_code == 200
    body = result_resp.json()
    assert body["status"] == "success"
    assert body["identity"] == {"sub": "42", "name": "Test User", "email": "test@example.com"}


# ---------------------------------------------------------------------------
# Test 4: failed token exchange marks session as failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_failure_marks_session(admin_client, test_app):
    """When token exchange returns no access_token, result is failed."""
    client, token = admin_client

    start_resp = await client.post(
        "/api/admin/test/oauth/github/start",
        json={"client_id": "cid", "client_secret": "bad-sec", "issuer_url": "", "name": "GitHub"},
        cookies={"access_token": token},
    )
    test_session_id = start_resp.json()["test_session_id"]
    state = next(iter(_TEST_SESSIONS))

    mock_token_resp = MagicMock(spec=Response)
    mock_token_resp.json.return_value = {"error": "invalid_client", "error_description": "bad credentials"}

    mock_http_client = AsyncMock()
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)
    mock_http_client.post = AsyncMock(return_value=mock_token_resp)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as raw_client:
        with patch("backend.config_mgmt.test_routes.httpx.AsyncClient", return_value=mock_http_client):
            cb_resp = await raw_client.get(
                "/api/auth/callback/github",
                params={"state": state, "code": "bad-code"},
            )
    assert cb_resp.status_code == 200

    result_resp = await client.get(
        f"/api/admin/test/oauth/{test_session_id}/result",
        cookies={"access_token": token},
    )
    assert result_resp.status_code == 200
    body = result_resp.json()
    assert body["status"] == "failed"
    assert "error" in body


# ---------------------------------------------------------------------------
# Test 5: result endpoint requires admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_result_endpoint_admin_only(test_app):
    """GET /result without auth returns 401/403."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/admin/test/oauth/fake-session-id/result")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Test 6: expired session returns 404 on callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_is_single_use(admin_client, test_app):
    """A second callback hit with the same state returns 404 — replay-resistant."""
    client, token = admin_client

    await client.post(
        "/api/admin/test/oauth/github/start",
        json={"client_id": "cid", "client_secret": "csec", "issuer_url": "", "name": "GitHub"},
        cookies={"access_token": token},
    )
    state = next(iter(_TEST_SESSIONS))

    # First callback hit: provider-side error path, marks status="failed".
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as raw_client:
        first = await raw_client.get(
            "/api/auth/callback/github",
            params={"state": state, "error": "access_denied"},
        )
        assert first.status_code == 200  # auto-close page rendered

        # Second hit with same state: session is no longer "live" so the
        # test dispatcher returns None and the unified callback falls
        # through to normal sign-in, which 400s on the missing cookie.
        second = await raw_client.get(
            "/api/auth/callback/github",
            params={"state": state, "code": "should-not-be-exchanged"},
        )
        assert second.status_code == 400


@pytest.mark.asyncio
async def test_session_expires_after_ttl(admin_client, test_app):
    """Fast-forwarding expires_at into the past causes callback to 404."""
    client, token = admin_client

    await client.post(
        "/api/admin/test/oauth/github/start",
        json={"client_id": "cid", "client_secret": "csec", "issuer_url": "", "name": "GitHub"},
        cookies={"access_token": token},
    )
    state = next(iter(_TEST_SESSIONS))

    # Manually expire the session
    _TEST_SESSIONS[state].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as raw_client:
        cb_resp = await raw_client.get(
            "/api/auth/callback/github",
            params={"state": state, "code": "code"},
        )
    # Expired session: dispatcher returns None, falls through to normal
    # sign-in which 400s on the missing cookie.
    assert cb_resp.status_code == 400


# ---------------------------------------------------------------------------
# Test 7: result endpoint returns pending immediately after start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_starts_with_pending_status(admin_client):
    """Immediately after start, the result endpoint reports pending."""
    client, token = admin_client

    start_resp = await client.post(
        "/api/admin/test/oauth/github/start",
        json={"client_id": "cid", "client_secret": "csec", "issuer_url": "", "name": "GitHub"},
        cookies={"access_token": token},
    )
    test_session_id = start_resp.json()["test_session_id"]

    result_resp = await client.get(
        f"/api/admin/test/oauth/{test_session_id}/result",
        cookies={"access_token": token},
    )
    assert result_resp.status_code == 200
    assert result_resp.json()["status"] == "pending"


# ---------------------------------------------------------------------------
# Test 8: SMTP test success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smtp_test_success(admin_client):
    """Mock smtplib so the send call succeeds; expect ok=True."""
    client, token = admin_client

    mock_smtp_instance = MagicMock()
    mock_smtp_instance.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_instance.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_smtp_instance):
        resp = await client.post(
            "/api/admin/test/smtp",
            json={
                "host": "smtp.example.com",
                "port": 587,
                "username": "user@example.com",
                "password": "pass",
                "from_address": "noreply@example.com",
                "use_tls": False,
                "to_address": "dest@example.com",
            },
            cookies={"access_token": token},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "error" not in body


# ---------------------------------------------------------------------------
# Test 9: SMTP test connection failure returns ok=False with error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smtp_test_connection_failure(admin_client):
    """When smtplib raises, response has ok=False and the error message."""
    client, token = admin_client

    with patch("smtplib.SMTP", side_effect=ConnectionRefusedError("Connection refused")):
        resp = await client.post(
            "/api/admin/test/smtp",
            json={
                "host": "bad-host.example.com",
                "port": 25,
                "username": "",
                "password": "",
                "from_address": "noreply@example.com",
                "use_tls": False,
                "to_address": "dest@example.com",
            },
            cookies={"access_token": token},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "error" in body
    assert len(body["error"]) > 0


# ---------------------------------------------------------------------------
# Test 10: SMTP test uses stored password when body.password is blank
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smtp_test_uses_stored_password_when_blank(admin_client, test_app):
    """Empty password in request body should fall back to the DB-stored value."""
    client, token = admin_client

    # Store a password in the DB
    with test_app.state.session_factory() as db:
        upsert_smtp_config(
            db,
            SmtpConfig(
                host="smtp.example.com",
                port=587,
                username="user@example.com",
                password="STORED-SECRET",
                from_address="noreply@example.com",
                use_tls=False,
            ),
        )

    mock_smtp_instance = MagicMock()
    mock_smtp_instance.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_instance.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_smtp_instance):
        resp = await client.post(
            "/api/admin/test/smtp",
            json={
                "host": "smtp.example.com",
                "port": 587,
                "username": "user@example.com",
                "password": "",  # blank — should use stored
                "from_address": "noreply@example.com",
                "use_tls": False,
                "to_address": "dest@example.com",
            },
            cookies={"access_token": token},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify login was called with the stored password
    mock_smtp_instance.login.assert_called_once_with("user@example.com", "STORED-SECRET")


# ---------------------------------------------------------------------------
# Groups.io test endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_groupsio_test_success(admin_client, test_app):
    """Stored credentials + a mocked successful HTTP round-trip → ok=True."""
    from backend.config_mgmt.service import set_config_value

    client, token = admin_client
    with test_app.state.session_factory() as db:
        set_config_value(db, "delivery.groupsio.api_key", "stored-key")
        set_config_value(db, "delivery.groupsio.group_name", "stored-group")

    mock_draft = MagicMock()
    mock_draft.status_code = 200
    mock_draft.json.return_value = {"id": 1, "group_id": 2}
    mock_draft.raise_for_status = MagicMock()
    mock_update = MagicMock()
    mock_update.status_code = 200
    mock_update.raise_for_status = MagicMock()
    mock_post = MagicMock()
    mock_post.status_code = 200
    mock_post.raise_for_status = MagicMock()

    with patch("backend.integrations.delivery.backends.groupsio.httpx") as mock_httpx:
        mock_httpx.post.side_effect = [mock_draft, mock_update, mock_post]
        resp = await client.post("/api/admin/test/groupsio", cookies={"access_token": token})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Confirm it used the saved credentials and the canonical test body.
    first_call = mock_httpx.post.call_args_list[0]
    assert first_call.kwargs["headers"]["Authorization"] == "Bearer stored-key"
    assert first_call.kwargs["data"]["group_name"] == "stored-group"
    update_call = mock_httpx.post.call_args_list[1]
    assert update_call.kwargs["data"]["body"] == "Test Message. Sorry for the noise, please disregard."


@pytest.mark.asyncio
async def test_groupsio_test_failure_surfaces_error(admin_client, test_app):
    """When the backend fails, the route returns ok=False with the error message."""
    from backend.config_mgmt.service import set_config_value

    client, token = admin_client
    with test_app.state.session_factory() as db:
        set_config_value(db, "delivery.groupsio.api_key", "stored-key")
        set_config_value(db, "delivery.groupsio.group_name", "stored-group")

    with patch("backend.integrations.delivery.backends.groupsio.httpx") as mock_httpx:
        mock_httpx.post.side_effect = Exception("network down")
        resp = await client.post("/api/admin/test/groupsio", cookies={"access_token": token})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "network down" in body["error"]


@pytest.mark.asyncio
async def test_groupsio_test_no_api_key(admin_client):
    """No stored API key → backend returns 'not configured' without any HTTP call."""
    client, token = admin_client
    with patch("backend.integrations.delivery.backends.groupsio.httpx") as mock_httpx:
        resp = await client.post("/api/admin/test/groupsio", cookies={"access_token": token})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "not configured" in body["error"].lower()
    mock_httpx.post.assert_not_called()
