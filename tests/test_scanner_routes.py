import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.integrations.scanner.service import scanner_state
from backend.integrations.scanner.routes import scanner_router
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
    return factory


@pytest.fixture
def app(test_settings, db_setup):
    application = FastAPI()
    application.state.session_factory = db_setup
    application.state.settings = test_settings
    application.include_router(scanner_router, prefix="/api/scanner")
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _create_admin(db_setup):
    with db_setup() as session:
        user = User(
            callsign="ADMIN",
            oidc_subject="local|admin",
            name="Admin User",
            email="admin@test.com",
            role=UserRole.ADMIN,
        )
        session.add(user)
        session.commit()
    return user


def _auth_headers(test_settings, callsign="ADMIN", role="admin"):
    token = create_access_token(callsign, role, test_settings)
    return {"Cookie": f"access_token={token}"}


@pytest.mark.anyio
async def test_scanner_status(app, client, db_setup, test_settings):
    _create_admin(db_setup)

    scanner_state.running = False
    scanner_state.last_scan_time = None
    scanner_state.last_scan_count = None
    scanner_state.active_session_id = None

    resp = await client.get("/api/scanner/status", headers=_auth_headers(test_settings))
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] is False
    assert data["last_scan_time"] is None


@pytest.mark.anyio
async def test_scanner_status_with_data(app, client, db_setup, test_settings):
    _create_admin(db_setup)

    scanner_state.running = True
    scanner_state.last_scan_time = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    scanner_state.last_scan_count = 3
    scanner_state.active_session_id = 1

    resp = await client.get("/api/scanner/status", headers=_auth_headers(test_settings))
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] is True
    assert data["last_scan_count"] == 3


@pytest.mark.anyio
async def test_scanner_trigger(app, client, db_setup, test_settings):
    _create_admin(db_setup)

    with patch("backend.integrations.scanner.routes.run_scan", return_value=2) as mock_scan:
        resp = await client.post("/api/scanner/trigger", headers=_auth_headers(test_settings))

    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 2
    mock_scan.assert_called_once()


@pytest.mark.anyio
async def test_scanner_trigger_skipped(app, client, db_setup, test_settings):
    _create_admin(db_setup)

    with patch("backend.integrations.scanner.routes.run_scan", return_value=None):
        resp = await client.post("/api/scanner/trigger", headers=_auth_headers(test_settings))

    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] is None
    assert "skipped" in data["message"].lower()


@pytest.mark.anyio
async def test_scanner_requires_auth(client):
    resp = await client.get("/api/scanner/status")
    assert resp.status_code == 401
