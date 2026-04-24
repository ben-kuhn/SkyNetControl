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
from backend.config import Settings, ProviderSettings


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
        auth_google=ProviderSettings(enabled=True, client_id="test-gid", client_secret="test-gsec"),
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
    app.state.providers = {
        "google": {
            "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
            "client_id": "test-gid",
            "client_secret": "test-gsec",
            "scopes": "openid email profile",
            "label": "Google",
            "protocol": "oidc",
            "extract_subject": lambda d: str(d.get("sub", "")),
            "extract_name": lambda d: d.get("name", "Unknown"),
            "extract_email": lambda d: d.get("email", ""),
        },
    }
    app.include_router(auth_router, prefix="/api/auth")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_providers_returns_enabled(test_client):
    response = await test_client.get("/api/auth/providers")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "google"
    assert data[0]["label"] == "Google"


@pytest.mark.asyncio
async def test_login_unknown_provider_404(test_client):
    response = await test_client.get("/api/auth/login/unknown", follow_redirects=False)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_login_redirects_to_provider(test_client):
    response = await test_client.get("/api/auth/login/google", follow_redirects=False)
    assert response.status_code in (302, 307)
    location = response.headers.get("location", "")
    assert "accounts.google.com" in location


@pytest.mark.asyncio
async def test_callback_creates_pending_user(test_client, test_app, db_setup):
    _, factory = db_setup

    # Pre-seed an admin so the callback user is NOT the first user
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="google:admin-seed", name="Admin", role=UserRole.ADMIN))
        session.commit()

    mock_token_response = MagicMock()
    mock_token_response.status_code = 200
    mock_token_response.json.return_value = {"access_token": "fake-token", "token_type": "Bearer"}

    mock_userinfo_response = MagicMock()
    mock_userinfo_response.status_code = 200
    mock_userinfo_response.json.return_value = {
        "sub": "google-123",
        "name": "Test User",
        "email": "test@example.com",
    }

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
async def test_callback_existing_user_not_changed(test_client, test_app, db_setup):
    _, factory = db_setup

    with factory() as session:
        user = User(callsign="W0NE", oidc_subject="google:existing-123", name="Existing", role=UserRole.ADMIN)
        session.add(user)
        session.commit()

    mock_token_response = MagicMock()
    mock_token_response.status_code = 200
    mock_token_response.json.return_value = {"access_token": "fake-token", "token_type": "Bearer"}

    mock_userinfo_response = MagicMock()
    mock_userinfo_response.status_code = 200
    mock_userinfo_response.json.return_value = {"sub": "existing-123", "name": "Existing", "email": "e@e.com"}

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
async def test_viewer_cannot_update_roles(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="auth0|admin", name="Admin", role=UserRole.ADMIN))
        session.add(User(callsign="KD0TST", oidc_subject="auth0|viewer", name="Viewer", role=UserRole.VIEWER))
        session.commit()

    token = create_access_token("KD0TST", "viewer", test_settings)
    response = await test_client.patch("/api/auth/users/W0NE", json={"role": "viewer"}, cookies={"access_token": token})
    assert response.status_code == 403
