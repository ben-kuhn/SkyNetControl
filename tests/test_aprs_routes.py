"""APRS routes tests — rewritten for net-independent /api/events routes."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.integrations.aprs import manager
from backend.integrations.aprs.store import EventPositionStore
from backend.modules.events.routes import events_router
from tests.conftest import make_test_token

BASE = "/api/events"


@pytest.fixture
def app_s():
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([
            User(callsign="W0NC", oidc_subject="auth0|nc", name="NC"),
            User(callsign="KD0TST", oidc_subject="auth0|v", name="V"),
        ])
        session.commit()
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = settings
    app.include_router(events_router)
    return app, settings, factory


@pytest.fixture(autouse=True)
def _clean_states():
    manager._states.clear()
    yield
    manager._states.clear()


def _c(app, settings, callsign=None):
    ck = {"access_token": make_test_token(callsign, settings)} if callsign else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=ck)


@pytest.fixture
async def active_event(app_s):
    """Creates an ACTIVE event owned by W0NC; returns (app, settings, factory, event_id)."""
    app, settings, factory = app_s
    async with _c(app, settings, "W0NC") as c:
        r = await c.post(BASE, json={"name": "E", "event_type": "emergency"})
        assert r.status_code == 201
        eid = r.json()["id"]
        r2 = await c.post(f"{BASE}/{eid}/activate")
        assert r2.status_code == 200
    return app, settings, factory, eid


class TestPositionsRoute:
    async def test_disabled_when_no_client(self, active_event):
        app, settings, factory, eid = active_event
        async with _c(app, settings, "W0NC") as c:
            resp = await c.get(f"{BASE}/{eid}/positions")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "stations": [], "latest_pos_seq": 0,
            "aprs_status": "disabled", "aprs_status_detail": "", "objects": [],
        }

    async def test_snapshot_from_running_state(self, active_event):
        app, settings, factory, eid = active_event
        state = manager.AprsClientState(event_id=eid, session_factory=factory)
        state.status = "connected"
        state.store.add_point("KE0XYZ-9", 39.0, -94.0, kind="participant", callsign="KE0XYZ")
        state.objects_by_post = {1: "RESTSTOP3"}
        manager._states[eid] = state

        async with _c(app, settings, "W0NC") as c:
            resp = await c.get(f"{BASE}/{eid}/positions", params={"since": 0})
        body = resp.json()
        assert body["aprs_status"] == "connected"
        assert body["latest_pos_seq"] == 1
        assert body["stations"][0]["station_id"] == "KE0XYZ-9"
        assert body["objects"] == [{"post_id": 1, "name": "RESTSTOP3"}]

        # Cursor: no new points, but roster still complete
        async with _c(app, settings, "W0NC") as c:
            resp = await c.get(f"{BASE}/{eid}/positions", params={"since": 1})
        body = resp.json()
        assert body["stations"][0]["points"] == []

    async def test_missing_event_404(self, app_s):
        app, settings, factory = app_s
        async with _c(app, settings, "W0NC") as c:
            assert (await c.get(f"{BASE}/9999/positions")).status_code == 404

    async def test_anonymous_401(self, app_s):
        """Anonymous users cannot access private event positions."""
        app, settings, factory = app_s
        async with _c(app, settings, "W0NC") as c:
            r = await c.post(BASE, json={"name": "E", "event_type": "emergency"})
            eid = r.json()["id"]
            await c.post(f"{BASE}/{eid}/activate")
        # Anonymous — no token
        async with _c(app, settings) as c:
            assert (await c.get(f"{BASE}/{eid}/positions")).status_code in (401, 404)


class TestLifecycleHooks:
    async def test_activate_calls_ensure_started(self, app_s, monkeypatch):
        app, settings, factory = app_s
        calls = []
        monkeypatch.setattr(manager, "ensure_started", lambda sf, eid: calls.append(("start", eid)))
        async with _c(app, settings, "W0NC") as c:
            r = await c.post(BASE, json={"name": "E", "event_type": "emergency"})
            event_id = r.json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
        assert ("start", event_id) in calls

    async def test_close_calls_stop_and_reopen_restarts(self, app_s, monkeypatch):
        app, settings, factory = app_s
        calls = []
        monkeypatch.setattr(manager, "ensure_started", lambda sf, eid: calls.append(("start", eid)))
        monkeypatch.setattr(manager, "stop", lambda eid: calls.append(("stop", eid)))
        async with _c(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json={"name": "E", "event_type": "emergency"})).json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
            await c.post(f"{BASE}/{event_id}/close")
            assert ("stop", event_id) in calls
            await c.post(f"{BASE}/{event_id}/reopen")
        assert calls.count(("start", event_id)) == 2

    async def test_checkin_and_posts_and_patch_nudge(self, app_s, monkeypatch):
        app, settings, factory = app_s
        nudges = []
        monkeypatch.setattr(manager, "ensure_started", lambda sf, eid: None)
        monkeypatch.setattr(manager, "nudge", lambda eid: nudges.append(eid))
        async with _c(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json={"name": "E", "event_type": "emergency"})).json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
            await c.post(f"{BASE}/{event_id}/participants", json={"callsign": "KE0XYZ"})
            post_id = (await c.post(
                f"{BASE}/{event_id}/posts", json={"name": "EOC", "lat": 39.0, "lon": -94.0}
            )).json()["id"]
            await c.patch(f"{BASE}/{event_id}/posts/{post_id}", json={"lat": 39.1})
            await c.patch(f"{BASE}/{event_id}", json={"aprs_beacon_posts": True})
            await c.delete(f"{BASE}/{event_id}/posts/{post_id}")
        assert nudges.count(event_id) >= 5
