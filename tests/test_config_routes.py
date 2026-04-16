import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.config_mgmt.routes import config_router
from backend.config import Settings


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
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
    # Seed admin and viewer users
    with factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            role=UserRole.ADMIN,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
            role=UserRole.VIEWER,
        )
        session.add_all([admin, viewer])
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(config_router, prefix="/api/config")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_admin_can_get_config(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/config/", cookies={"access_token": token}
    )
    assert response.status_code == 200
    assert isinstance(response.json(), dict)


@pytest.mark.asyncio
async def test_admin_can_set_config(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.put(
        "/api/config/net_address",
        json={"value": "w0ne@winlink.org"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200

    response = await test_client.get(
        "/api/config/", cookies={"access_token": token}
    )
    assert response.json()["net_address"] == "w0ne@winlink.org"


@pytest.mark.asyncio
async def test_viewer_cannot_set_config(test_client, test_settings):
    token = create_access_token("KD0TST", "viewer", test_settings)
    response = await test_client.put(
        "/api/config/net_address",
        json={"value": "hacker@winlink.org"},
        cookies={"access_token": token},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_get_config(test_client):
    response = await test_client.get("/api/config/")
    assert response.status_code == 401
