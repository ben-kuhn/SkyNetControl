import json
from datetime import date
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.auth.service import create_access_token
from backend.config import Settings
from backend.config_mgmt.models import AppConfig
from backend.modules.checkins.routes import checkins_router
from tests.conftest import make_test_token



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
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from backend.modules.nets.models import Net, NetMembership, NetRole
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="auth0|admin", name="Admin", is_admin=True))
        session.add(User(callsign="W0NC", oidc_subject="auth0|nc", name="Net Control"))
        session.add(User(callsign="KD0TST", oidc_subject="auth0|viewer", name="Viewer"))
        session.add(User(callsign="W0NOMEMBER", oidc_subject="auth0|nomember", name="No Member"))
        net = Net(slug="default", name="Default Net")
        session.add(net)
        session.flush()
        session.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        session.add(NetMembership(user_callsign="KD0TST", net_id=net.id, role=NetRole.VIEWER))
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(checkins_router, prefix="/api/checkins")
    return app


@pytest.fixture
async def client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_lookup_success(client, test_settings):
    token = make_test_token("W0NC", test_settings, token_version=0)
    mock_result = {
        "callsign": "W0ABC",
        "name": "John Smith",
        "city": "Denver",
        "county": "Denver",
        "state": "CO",
        "country": "United States",
        "latitude": 39.7392,
        "longitude": -104.9903,
        "source": "hamqth",
        "cached": False,
    }

    with patch("backend.modules.checkins.routes.is_callbook_configured", return_value=True), patch(
        "backend.modules.checkins.routes.lookup_callsign", return_value=mock_result
    ):
        resp = await client.get("/api/checkins/lookup/W0ABC", cookies={"access_token": token})

    assert resp.status_code == 200
    assert resp.json()["name"] == "John Smith"


@pytest.mark.asyncio
async def test_lookup_not_found(client, test_settings):
    token = make_test_token("W0NC", test_settings, token_version=0)

    with patch("backend.modules.checkins.routes.is_callbook_configured", return_value=True), patch(
        "backend.modules.checkins.routes.lookup_callsign", return_value=None
    ):
        resp = await client.get("/api/checkins/lookup/XXXXXX", cookies={"access_token": token})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_lookup_503_when_not_configured(client, test_settings):
    """Operator who hasn't configured a provider should get an actionable 503,
    not the 404 'not found' that masks the real cause (backlog item 4)."""
    token = make_test_token("W0NC", test_settings, token_version=0)

    resp = await client.get("/api/checkins/lookup/W0ABC", cookies={"access_token": token})

    assert resp.status_code == 503
    assert "Config" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_lookup_viewer_member_allowed(client, test_settings):
    """VIEWER-role net members can now access callbook lookup (require_net_member allows all members)."""
    token = make_test_token("KD0TST", test_settings, token_version=0)

    mock_result = {
        "callsign": "W0ABC", "name": "Test", "city": "Denver", "county": None,
        "state": "CO", "country": "US", "latitude": None, "longitude": None,
        "source": "hamqth", "cached": False,
    }
    with patch("backend.modules.checkins.routes.is_callbook_configured", return_value=True), \
         patch("backend.modules.checkins.routes.lookup_callsign", return_value=mock_result):
        resp = await client.get("/api/checkins/lookup/W0ABC", cookies={"access_token": token})

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_lookup_non_member_denied(client, test_settings):
    """Users with no net membership cannot access callbook lookup."""
    token = make_test_token("W0NOMEMBER", test_settings, token_version=0)
    resp = await client.get("/api/checkins/lookup/W0ABC", cookies={"access_token": token})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_lookup_admin_allowed(client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    mock_result = {
        "callsign": "W0ABC",
        "name": "John",
        "city": "Denver",
        "county": None,
        "state": "CO",
        "country": "US",
        "latitude": None,
        "longitude": None,
        "source": "qrz",
        "cached": True,
    }

    with patch("backend.modules.checkins.routes.is_callbook_configured", return_value=True), patch(
        "backend.modules.checkins.routes.lookup_callsign", return_value=mock_result
    ):
        resp = await client.get("/api/checkins/lookup/W0ABC", cookies={"access_token": token})

    assert resp.status_code == 200
    assert resp.json()["cached"] is True
