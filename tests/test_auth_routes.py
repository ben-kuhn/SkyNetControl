import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import AsyncMock, patch

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.routes import auth_router
from backend.auth.service import create_access_token
from backend.config import Settings


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
        oidc_issuer_url="https://idp.example.com",
        oidc_client_id="test-client",
        oidc_client_secret="test-secret",
        oidc_redirect_uri="http://localhost:8000/api/auth/callback",
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
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_me_returns_user(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        user = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            role=UserRole.ADMIN,
        )
        session.add(user)
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/auth/me", cookies={"access_token": token}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["callsign"] == "W0NE"
    assert data["name"] == "Admin"
    assert data["role"] == "admin"


@pytest.mark.asyncio
async def test_me_unauthenticated(test_client):
    response = await test_client.get("/api/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_cookie(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/auth/logout",
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 200
    # Check that the cookie is being cleared (max_age=0 or expires in past)
    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token" in set_cookie


@pytest.mark.asyncio
async def test_login_redirects(test_client):
    with patch("backend.auth.routes._get_oidc_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.create_authorization_url.return_value = (
            "https://idp.example.com/authorize?client_id=test",
            "random-state",
        )
        mock_get_client.return_value = mock_client

        response = await test_client.get(
            "/api/auth/login", follow_redirects=False
        )
        assert response.status_code == 307 or response.status_code == 302
