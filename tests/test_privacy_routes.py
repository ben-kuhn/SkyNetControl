import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.modules.checkins.models import (
    CheckIn,
    ParseStatus,
    TimingStatus,
)
from backend.modules.schedule.models import NetSession
from backend.privacy.routes import privacy_router
from backend.config import Settings
from datetime import datetime, timezone


@pytest.fixture
def route_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )


@pytest.fixture
def route_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin User",
            role=UserRole.ADMIN,
            email="admin@example.com",
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Test User",
            role=UserRole.VIEWER,
            email="viewer@example.com",
        )
        session.add_all([admin, viewer])
        session.flush()

        ns = NetSession(
            id=1,
            start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            grace_period_hours=1,
            session_type="regular",
            status="closed",
        )
        session.add(ns)
        session.flush()

        ci = CheckIn(
            session_id=1,
            callsign="KD0TST",
            name="Test User",
            city="Denver",
            mode="Winlink",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
        )
        session.add(ci)
        session.commit()
    return factory


@pytest.fixture
def route_app(route_settings, route_db):
    app = FastAPI()
    app.state.session_factory = route_db
    app.state.settings = route_settings
    app.include_router(privacy_router, prefix="/api/privacy")
    return app


@pytest.fixture
async def route_client(route_app):
    transport = ASGITransport(app=route_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_self_export(route_client, route_settings):
    token = create_access_token("KD0TST", "viewer", route_settings)
    response = await route_client.get("/api/privacy/export", cookies={"access_token": token})
    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")
    data = response.json()
    assert data["user"]["callsign"] == "KD0TST"


@pytest.mark.asyncio
async def test_admin_export_other_user(route_client, route_settings):
    token = create_access_token("W0NE", "admin", route_settings)
    response = await route_client.get("/api/privacy/export/KD0TST", cookies={"access_token": token})
    assert response.status_code == 200
    data = response.json()
    assert data["user"]["callsign"] == "KD0TST"


@pytest.mark.asyncio
async def test_viewer_cannot_export_other_user(route_client, route_settings):
    token = create_access_token("KD0TST", "viewer", route_settings)
    response = await route_client.get("/api/privacy/export/W0NE", cookies={"access_token": token})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_export(route_client):
    response = await route_client.get("/api/privacy/export")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_self_anonymize(route_client, route_settings):
    token = create_access_token("KD0TST", "viewer", route_settings)
    response = await route_client.post(
        "/api/privacy/anonymize",
        json={"confirm": True},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["anonymized"] is True
    assert data["anonymous_id"].startswith("ANON-")
    # Cookie should be cleared
    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token" in set_cookie


@pytest.mark.asyncio
async def test_self_anonymize_requires_confirm(route_client, route_settings):
    token = create_access_token("KD0TST", "viewer", route_settings)
    response = await route_client.post(
        "/api/privacy/anonymize",
        json={"confirm": False},
        cookies={"access_token": token},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_self_anonymize_without_body(route_client, route_settings):
    token = create_access_token("KD0TST", "viewer", route_settings)
    response = await route_client.post(
        "/api/privacy/anonymize",
        json={},
        cookies={"access_token": token},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_admin_anonymize_other_user(route_client, route_settings):
    token = create_access_token("W0NE", "admin", route_settings)
    response = await route_client.post(
        "/api/privacy/anonymize/KD0TST",
        json={"confirm": True},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["anonymized"] is True


@pytest.mark.asyncio
async def test_viewer_cannot_anonymize_other(route_client, route_settings):
    token = create_access_token("KD0TST", "viewer", route_settings)
    response = await route_client.post(
        "/api/privacy/anonymize/W0NE",
        json={"confirm": True},
        cookies={"access_token": token},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_anonymize(route_client):
    response = await route_client.post("/api/privacy/anonymize", json={"confirm": True})
    assert response.status_code == 401
