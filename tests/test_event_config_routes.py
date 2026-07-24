"""Tests for GET/PUT /api/events/{id}/config routes (per-event config, CONTROL-gated)."""
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import Settings
from backend.db.base import Base
from backend.auth.models import User
from backend.modules.events.models import Event, EventOperator, EventType, EventStatus
from backend.modules.events.event_auth import EventRole, EventContext, require_event_role
from backend.auth.dependencies import get_db_session
from backend.modules.events.config_routes import event_config_router
from tests.conftest import make_test_token


@pytest.fixture
def app_ctx():
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret")
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all(
            [
                User(callsign="OWNER", oidc_subject="x|o", name="O"),
                User(callsign="OTHER", oidc_subject="x|ot", name="T"),
            ]
        )
        priv = Event(
            name="P",
            event_type=EventType.EMERGENCY,
            status=EventStatus.ACTIVE,
            created_by="OWNER",
            public_token="ptok",
            visibility="private",
        )
        pub = Event(
            name="U",
            event_type=EventType.EMERGENCY,
            status=EventStatus.ACTIVE,
            created_by="OWNER",
            public_token="utok",
            visibility="public",
        )
        db.add_all([priv, pub])
        db.flush()
        db.commit()
        ids = {"event": priv.id, "public_event": pub.id}
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = settings
    app.include_router(event_config_router)
    return app, settings, ids, factory


def _control_client(app, settings):
    """Authenticated client as OWNER (CONTROL on the private event)."""
    token = make_test_token("OWNER", settings)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"access_token": token})


def _other_client(app, settings):
    """Authenticated client as OTHER (non-control on any event)."""
    token = make_test_token("OTHER", settings)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"access_token": token})


@pytest.mark.asyncio
async def test_get_config_masks_secrets(app_ctx):
    app, settings, ids, factory = app_ctx
    eid = ids["event"]
    async with _control_client(app, settings) as c:
        # seed via PUT
        r = await c.put(f"/api/events/{eid}/config/bulk", json={"values": {
            "net_address": "W0EVT",
            "pat_http_password": "s3cret",
        }})
        assert r.status_code == 200
        r = await c.get(f"/api/events/{eid}/config")
        assert r.status_code == 200
        body = r.json()
        assert body["net_address"] == "W0EVT"
        assert body["pat_http_password"] == "***"  # masked, never plaintext


@pytest.mark.asyncio
async def test_put_encrypts_secret_at_rest_and_roundtrips(app_ctx):
    app, settings, ids, factory = app_ctx
    eid = ids["event"]
    async with _control_client(app, settings) as c:
        await c.put(f"/api/events/{eid}/config/bulk", json={"values": {"pat_http_password": "s3cret"}})
    # stored value is encrypted (not the plaintext); get_event_config decrypts it back
    from backend.modules.events.event_config_service import get_event_config
    with factory() as db:
        assert get_event_config(db, eid, "pat_http_password") == "s3cret"


@pytest.mark.asyncio
async def test_config_requires_control(app_ctx):
    app, settings, ids, factory = app_ctx
    eid, pub = ids["event"], ids["public_event"]
    # authenticated non-control on a public event: 403 on read, 403 on write
    async with _other_client(app, settings) as c:
        assert (await c.get(f"/api/events/{pub}/config")).status_code == 403
        assert (await c.put(f"/api/events/{pub}/config/bulk", json={"values": {}})).status_code == 403
