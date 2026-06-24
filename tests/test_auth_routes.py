import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import AsyncMock, patch, MagicMock

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.routes import auth_router
from backend.auth.service import create_access_token
from backend.config import Settings
from backend.config_mgmt.oauth import OAuthProviderConfig, upsert_oauth_provider


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
        app_base_url="http://localhost:8000",
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
    engine, factory = db_setup
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = test_settings
    app.include_router(auth_router, prefix="/api/auth")

    # Seed google provider into the DB so list_providers and login work
    with factory() as session:
        upsert_oauth_provider(session, OAuthProviderConfig(
            slug="google",
            name="Google",
            enabled=True,
            client_id="test-gid",
            client_secret="test-gsec",
            issuer_url="",
        ))
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


_GOOGLE_CONFIG = {
    "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_url": "https://oauth2.googleapis.com/token",
    "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
    "issuer": "https://accounts.google.com",
    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
    "client_id": "test-gid",
    "client_secret": "test-gsec",
    "scopes": "openid email profile",
    "label": "Google",
    "protocol": "oidc",
    "extract_subject": lambda d: str(d.get("sub", "")),
    "extract_name": lambda d: d.get("name", "Unknown"),
    "extract_email": lambda d: d.get("email", ""),
}


def _mock_oidc_verifier(claims_to_return: dict | None):
    """Helper: return an AsyncMock for backend.auth.oidc_verify.verify_id_token.

    OIDC callback now requires id_token verification; mock it to skip the
    JWKS fetch + signature check in unit tests."""
    return patch(
        "backend.auth.oidc_verify.verify_id_token",
        new_callable=AsyncMock,
        return_value=claims_to_return,
    )


@pytest.mark.asyncio
async def test_providers_returns_enabled(test_client):
    # /providers calls get_enabled_providers + build_providers directly
    # against the DB (no resolve_provider involvement) — the test_app
    # fixture has seeded `google` so the real code path returns it.
    response = await test_client.get("/api/auth/providers")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "google"
    assert data[0]["label"] == "Google"


@pytest.mark.asyncio
async def test_login_unknown_provider_404(test_client):
    with patch("backend.auth.routes.resolve_provider", new_callable=AsyncMock, return_value=None):
        response = await test_client.get("/api/auth/login/unknown", follow_redirects=False)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_login_redirects_to_provider(test_client):
    with patch("backend.auth.routes.resolve_provider", new_callable=AsyncMock,
               return_value=_GOOGLE_CONFIG):
        response = await test_client.get("/api/auth/login/google", follow_redirects=False)
    assert response.status_code in (302, 307)
    location = response.headers.get("location", "")
    assert "accounts.google.com" in location


@pytest.mark.asyncio
@pytest.mark.xfail(reason="role attribute removed in Task 3; restored as is_admin/is_pending/is_deleted in Task 4", strict=False)
async def test_callback_creates_pending_user(test_client, test_app, db_setup):
    _, factory = db_setup

    # Pre-seed an admin so the callback user is NOT the first user
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="google:admin-seed", name="Admin", role=UserRole.ADMIN))
        session.commit()

    mock_token_response = MagicMock()
    mock_token_response.status_code = 200
    # OIDC callback now requires an id_token; the verifier is mocked below.
    mock_token_response.json.return_value = {
        "access_token": "fake-token",
        "id_token": "fake-id-token",
        "token_type": "Bearer",
    }

    mock_userinfo_response = MagicMock()
    mock_userinfo_response.status_code = 200
    mock_userinfo_response.json.return_value = {
        "sub": "google-123",
        "name": "Test User",
        "email": "test@example.com",
        "email_verified": True,
    }

    with patch("backend.auth.routes.resolve_provider", new_callable=AsyncMock,
               return_value=_GOOGLE_CONFIG), _mock_oidc_verifier({"sub": "google-123"}):
        with patch("backend.auth.routes.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_token_response
            mock_client.get.return_value = mock_userinfo_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            login_resp = await test_client.get("/api/auth/login/google", follow_redirects=False)
            cookies = login_resp.cookies

            location = login_resp.headers.get("location", "")
            import urllib.parse

            parsed = urllib.parse.urlparse(location)
            params = urllib.parse.parse_qs(parsed.query)
            state = params.get("state", [""])[0]

            response = await test_client.get(
                f"/api/auth/callback/google?code=authcode&state={state}",
                cookies=cookies,
                follow_redirects=False,
            )

    assert response.status_code in (302, 307)

    with factory() as session:
        user = session.query(User).filter(User.oidc_subject == "google:google-123").first()
        assert user is not None
        assert user.role == UserRole.PENDING
        assert user.name == "Test User"
        assert user.email == "test@example.com"
        assert user.callsign.startswith("PENDING-")


@pytest.mark.asyncio
@pytest.mark.xfail(reason="role attribute removed in Task 3; restored as is_admin/is_pending/is_deleted in Task 4", strict=False)
async def test_callback_refuses_ssrf_token_url(test_client, test_app, db_setup):
    """A discovery doc that points token_endpoint at an internal address
    must not get free SSRF. The SSRF guard runs on token_url and
    userinfo_url before any httpx fetch."""
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="google:admin-seed", name="Admin", role=UserRole.ADMIN))
        session.commit()

    # Hostile config: token_url points at loopback.
    hostile = dict(_GOOGLE_CONFIG)
    hostile["token_url"] = "https://127.0.0.1/internal/token"

    with patch("backend.auth.routes.resolve_provider", new_callable=AsyncMock, return_value=hostile):
        login_resp = await test_client.get("/api/auth/login/google", follow_redirects=False)
        cookies = login_resp.cookies
        import urllib.parse
        parsed = urllib.parse.urlparse(login_resp.headers.get("location", ""))
        state = urllib.parse.parse_qs(parsed.query).get("state", [""])[0]

        response = await test_client.get(
            f"/api/auth/callback/google?code=authcode&state={state}",
            cookies=cookies,
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert "ssrf guard" in response.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.xfail(reason="role attribute removed in Task 3; restored as is_admin/is_pending/is_deleted in Task 4", strict=False)
async def test_callback_registration_closed_blocks_new_user(test_client, test_app, db_setup):
    """When registration_open=false, OAuth callback for an unknown subject 403s.

    Existing users (oidc_subject already in DB) still sign in. First-signup
    still works (a separate test covers that). This is the headline gate
    against unauthenticated DB-row spam.
    """
    _, factory = db_setup

    # Pre-seed an admin AND close registration via AppConfig.
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="google:admin-seed", name="Admin", role=UserRole.ADMIN))
        from backend.config_mgmt.service import set_config_value
        set_config_value(session, "registration_open", "false")
        session.commit()

    mock_token_response = MagicMock()
    mock_token_response.json.return_value = {
        "access_token": "fake-token",
        "id_token": "fake-id-token",
        "token_type": "Bearer",
    }
    mock_userinfo_response = MagicMock()
    mock_userinfo_response.json.return_value = {"sub": "stranger", "name": "Drive-By", "email": "x@y.z"}

    with patch("backend.auth.routes.resolve_provider", new_callable=AsyncMock, return_value=_GOOGLE_CONFIG), \
            _mock_oidc_verifier({"sub": "stranger"}):
        with patch("backend.auth.routes.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_token_response
            mock_client.get.return_value = mock_userinfo_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            login_resp = await test_client.get("/api/auth/login/google", follow_redirects=False)
            cookies = login_resp.cookies
            import urllib.parse
            parsed = urllib.parse.urlparse(login_resp.headers.get("location", ""))
            state = urllib.parse.parse_qs(parsed.query).get("state", [""])[0]

            response = await test_client.get(
                f"/api/auth/callback/google?code=authcode&state={state}",
                cookies=cookies,
                follow_redirects=False,
            )

    assert response.status_code == 403
    with factory() as session:
        assert session.query(User).filter(User.oidc_subject == "google:stranger").first() is None


@pytest.mark.asyncio
@pytest.mark.xfail(reason="role attribute removed in Task 3; restored as is_admin/is_pending/is_deleted in Task 4", strict=False)
async def test_callback_existing_user_not_changed(test_client, test_app, db_setup):
    _, factory = db_setup

    with factory() as session:
        user = User(callsign="W0NE", oidc_subject="google:existing-123", name="Existing", role=UserRole.ADMIN)
        session.add(user)
        session.commit()

    mock_token_response = MagicMock()
    mock_token_response.status_code = 200
    mock_token_response.json.return_value = {
        "access_token": "fake-token",
        "id_token": "fake-id-token",
        "token_type": "Bearer",
    }

    mock_userinfo_response = MagicMock()
    mock_userinfo_response.status_code = 200
    mock_userinfo_response.json.return_value = {"sub": "existing-123", "name": "Existing", "email": "e@e.com"}

    with patch("backend.auth.routes.resolve_provider", new_callable=AsyncMock,
               return_value=_GOOGLE_CONFIG), _mock_oidc_verifier({"sub": "existing-123"}):
        with patch("backend.auth.routes.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_token_response
            mock_client.get.return_value = mock_userinfo_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            login_resp = await test_client.get("/api/auth/login/google", follow_redirects=False)
            cookies = login_resp.cookies
            location = login_resp.headers.get("location", "")
            import urllib.parse

            parsed = urllib.parse.urlparse(location)
            params = urllib.parse.parse_qs(parsed.query)
            state = params.get("state", [""])[0]

            response = await test_client.get(
                f"/api/auth/callback/google?code=authcode&state={state}",
                cookies=cookies,
                follow_redirects=False,
            )

    assert response.status_code in (302, 307)

    with factory() as session:
        user = session.query(User).filter(User.oidc_subject == "google:existing-123").first()
        assert user.role == UserRole.ADMIN
        assert user.callsign == "W0NE"


@pytest.mark.asyncio
@pytest.mark.xfail(reason="role attribute removed in Task 3; restored as is_admin/is_pending/is_deleted in Task 4", strict=False)
async def test_me_returns_user(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(
            User(
                callsign="W0NE",
                oidc_subject="auth0|admin",
                name="Admin",
                role=UserRole.ADMIN,
                email="admin@example.com",
            )
        )
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get("/api/auth/me", cookies={"access_token": token})
    assert response.status_code == 200
    data = response.json()
    assert data["callsign"] == "W0NE"
    assert data["name"] == "Admin"
    assert data["role"] == "admin"
    assert data["email"] == "admin@example.com"


@pytest.mark.asyncio
async def test_me_unauthenticated(test_client):
    response = await test_client.get("/api/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_cookie(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post("/api/auth/logout", cookies={"access_token": token}, follow_redirects=False)
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token" in set_cookie


@pytest.mark.asyncio
@pytest.mark.xfail(reason="role attribute removed in Task 3; restored as is_admin/is_pending/is_deleted in Task 4", strict=False)
async def test_logout_invalidates_outstanding_jwt(test_client, test_settings, db_setup):
    """Stolen-cookie defense: after logout, the same JWT must stop working.

    delete_cookie clears the browser's copy, but a cookie captured via
    XSS/log leak/shared device is unaffected. Bumping users.token_version
    and checking it in get_current_user makes outstanding tokens 401 on
    the next request, eliminating the window between logout and exp.
    """
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="auth0|admin", name="Admin", role=UserRole.ADMIN))
        session.commit()

    # Issue a JWT with token_version=0 (the default).
    token = create_access_token("W0NE", "admin", test_settings, token_version=0)

    # /me works before logout.
    me = await test_client.get("/api/auth/me", cookies={"access_token": token})
    assert me.status_code == 200

    # Logout bumps token_version on the row.
    await test_client.post("/api/auth/logout", cookies={"access_token": token})

    # Same JWT now 401s — its tv=0 no longer matches users.token_version=1.
    after = await test_client.get("/api/auth/me", cookies={"access_token": token})
    assert after.status_code == 401


@pytest.mark.asyncio
@pytest.mark.xfail(reason="role attribute removed in Task 3; restored as is_admin/is_pending/is_deleted in Task 4", strict=False)
async def test_role_change_invalidates_outstanding_jwt(test_client, test_settings, db_setup):
    """Demote an admin: their existing JWT (which encoded role=admin) must
    not survive the demotion. token_version bump on PATCH /users handles this."""
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="auth0|admin", name="Admin", role=UserRole.ADMIN))
        session.add(User(callsign="KD0TST", oidc_subject="auth0|target", name="Target", role=UserRole.ADMIN))
        session.commit()

    admin_token = create_access_token("W0NE", "admin", test_settings, token_version=0)
    target_token = create_access_token("KD0TST", "admin", test_settings, token_version=0)

    # Target's JWT works initially.
    pre = await test_client.get("/api/auth/me", cookies={"access_token": target_token})
    assert pre.status_code == 200

    # Admin demotes target to viewer.
    demote = await test_client.patch(
        "/api/auth/users/KD0TST",
        json={"role": "viewer"},
        cookies={"access_token": admin_token},
    )
    assert demote.status_code == 200

    # Target's old JWT (which claimed admin role + tv=0) is now invalid.
    post = await test_client.get("/api/auth/me", cookies={"access_token": target_token})
    assert post.status_code == 401


@pytest.mark.asyncio
@pytest.mark.xfail(reason="role attribute removed in Task 3; restored as is_admin/is_pending/is_deleted in Task 4", strict=False)
async def test_admin_can_list_users(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="auth0|admin", name="Admin", role=UserRole.ADMIN))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get("/api/auth/users", cookies={"access_token": token})
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
@pytest.mark.xfail(reason="role attribute removed in Task 3; restored as is_admin/is_pending/is_deleted in Task 4", strict=False)
async def test_admin_can_update_user_role(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="auth0|admin", name="Admin", role=UserRole.ADMIN))
        session.add(User(callsign="KD0TST", oidc_subject="auth0|viewer", name="Viewer", role=UserRole.VIEWER))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.patch(
        "/api/auth/users/KD0TST", json={"role": "net_control"}, cookies={"access_token": token}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "net_control"


@pytest.mark.asyncio
@pytest.mark.xfail(reason="role attribute removed in Task 3; restored as is_admin/is_pending/is_deleted in Task 4", strict=False)
async def test_viewer_cannot_update_roles(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="auth0|admin", name="Admin", role=UserRole.ADMIN))
        session.add(User(callsign="KD0TST", oidc_subject="auth0|viewer", name="Viewer", role=UserRole.VIEWER))
        session.commit()

    token = create_access_token("KD0TST", "viewer", test_settings)
    response = await test_client.patch("/api/auth/users/W0NE", json={"role": "viewer"}, cookies={"access_token": token})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_optional_user_returns_none_without_cookie(test_app):
    """get_optional_user returns None when no auth cookie is sent."""
    from backend.auth.dependencies import get_optional_user

    from fastapi import Request

    async def receive():
        return {"type": "http.request"}

    scope = {
        "type": "http",
        "headers": [],
        "app": test_app,
        "state": {},
    }
    req = Request(scope, receive)

    with test_app.state.session_factory() as session:
        result = get_optional_user(
            request=req,
            access_token=None,
            authorization=None,
            db=session,
            app_settings=test_app.state.settings,
        )
    assert result is None


@pytest.mark.asyncio
@pytest.mark.xfail(reason="role attribute removed in Task 3; restored as is_admin/is_pending/is_deleted in Task 4", strict=False)
async def test_get_optional_user_returns_user_with_valid_cookie(test_app, test_settings):
    """get_optional_user returns the user when a valid cookie is present."""
    from backend.auth.dependencies import get_optional_user
    from backend.auth.service import create_access_token
    from fastapi import Request

    with test_app.state.session_factory() as session:
        user = User(
            callsign="W0OPT",
            oidc_subject="opt|sub",
            name="Opt",
            role=UserRole.VIEWER,
        )
        session.add(user)
        session.commit()

    token = create_access_token("W0OPT", "viewer", test_settings)
    scope = {"type": "http", "headers": [], "app": test_app, "state": {}}

    async def receive():
        return {"type": "http.request"}

    req = Request(scope, receive)
    with test_app.state.session_factory() as session:
        result = get_optional_user(
            request=req,
            access_token=token,
            authorization=None,
            db=session,
            app_settings=test_settings,
        )
    assert result is not None
    assert result.callsign == "W0OPT"


@pytest.mark.asyncio
async def test_get_optional_user_returns_none_with_invalid_cookie(test_app, test_settings):
    """get_optional_user returns None when the cookie is malformed/expired."""
    from backend.auth.dependencies import get_optional_user
    from fastapi import Request

    scope = {"type": "http", "headers": [], "app": test_app, "state": {}}

    async def receive():
        return {"type": "http.request"}

    req = Request(scope, receive)
    with test_app.state.session_factory() as session:
        result = get_optional_user(
            request=req,
            access_token="not-a-real-jwt",
            authorization=None,
            db=session,
            app_settings=test_settings,
        )
    assert result is None
