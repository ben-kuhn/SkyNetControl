"""APRS settings tests — rewritten for net-independent /api/events routes."""
import secrets

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.events.models import Event, EventType
from backend.modules.events.routes import events_router
from tests.conftest import make_test_token

BASE = "/api/events"


def test_aprslib_importable():
    import aprslib

    assert aprslib.passcode("N0CALL") == 13023


def test_event_aprs_defaults():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        event = Event(name="E", event_type=EventType.EMERGENCY, created_by="W0NE",
                      public_token=secrets.token_urlsafe(16))
        db.add(event)
        db.commit()
        db.refresh(event)
        assert event.aprs_other_stations is False
        assert event.aprs_beacon_posts is False
        assert event.aprs_range_lat is None
        assert event.aprs_range_lon is None
        assert event.aprs_range_km is None
    engine.dispose()


@pytest.fixture
def app_s():
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(User(callsign="W0NC", oidc_subject="auth0|nc", name="NC"))
        session.commit()
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = settings
    app.include_router(events_router)
    return app, settings


def _c(app, settings, callsign=None):
    ck = {"access_token": make_test_token(callsign, settings)} if callsign else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=ck)


class TestAprsSettingsPatch:
    async def test_response_includes_aprs_fields(self, app_s):
        app, settings = app_s
        async with _c(app, settings, "W0NC") as c:
            r = await c.post(BASE, json={"name": "E", "event_type": "emergency"})
            assert r.status_code == 201
            eid = r.json()["id"]
            await c.post(f"{BASE}/{eid}/activate")
            # Get snapshot — the event dict includes APRS fields
            snap = (await c.get(f"{BASE}/{eid}")).json()
        body = snap["event"]
        assert body["aprs_other_stations"] is False
        assert body["aprs_beacon_posts"] is False
        assert body["aprs_range_km"] is None

    async def test_patch_aprs_fields(self, app_s):
        app, settings = app_s
        async with _c(app, settings, "W0NC") as c:
            r = await c.post(BASE, json={"name": "E", "event_type": "emergency"})
            event_id = r.json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
            resp = await c.patch(f"{BASE}/{event_id}", json={
                "aprs_other_stations": True,
                "aprs_range_lat": 39.1,
                "aprs_range_lon": -94.6,
                "aprs_range_km": 50,
                "aprs_beacon_posts": True,
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["aprs_other_stations"] is True
        assert body["aprs_range_km"] == 50
        assert body["aprs_beacon_posts"] is True

    async def test_patch_partial_leaves_others(self, app_s):
        app, settings = app_s
        async with _c(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json={"name": "E", "event_type": "emergency"})).json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
            await c.patch(f"{BASE}/{event_id}", json={"aprs_beacon_posts": True})
            resp = await c.patch(f"{BASE}/{event_id}", json={"name": "Renamed"})
        assert resp.json()["aprs_beacon_posts"] is True
