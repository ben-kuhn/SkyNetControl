import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import Settings
from backend.db.base import Base
from backend.auth.models import User
from backend.modules.events.routes import events_router
from tests.conftest import make_test_token


@pytest.fixture
def app_s():
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret")
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([User(callsign="W0NC", oidc_subject="x|a", name="A"),
                    User(callsign="W0OUT", oidc_subject="x|o", name="O")])
        db.commit()
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = settings
    app.include_router(events_router)
    return app, settings


def _c(app, settings, callsign=None):
    ck = {"access_token": make_test_token(callsign, settings)} if callsign else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=ck)


@pytest.mark.asyncio
async def test_any_operator_creates_and_owns(app_s):
    app, settings = app_s
    async with _c(app, settings, "W0NC") as c:
        r = await c.post("/api/events", json={"name": "Skywarn", "event_type": "emergency"})
    assert r.status_code == 201
    body = r.json()
    assert body["owner"] == "W0NC" and body["visibility"] == "private" and body["is_control"] is True


@pytest.mark.asyncio
async def test_mine_lists_only_my_events(app_s):
    app, settings = app_s
    async with _c(app, settings, "W0NC") as c:
        await c.post("/api/events", json={"name": "E1", "event_type": "emergency"})
        mine = (await c.get("/api/events")).json()
    assert len(mine) == 1
    async with _c(app, settings, "W0OUT") as c:
        assert (await c.get("/api/events")).json() == []   # not owner/op


@pytest.mark.asyncio
async def test_owner_only_delete(app_s):
    app, settings = app_s
    async with _c(app, settings, "W0NC") as c:
        eid = (await c.post("/api/events", json={"name": "E", "event_type": "emergency"})).json()["id"]
    async with _c(app, settings, "W0OUT") as c:
        assert (await c.delete(f"/api/events/{eid}")).status_code in (403, 404)
    async with _c(app, settings, "W0NC") as c:
        assert (await c.delete(f"/api/events/{eid}")).status_code == 204


# ---------------------------------------------------------------------------
# Sub-resource routes — posts, participants, log, updates, report, positions,
# weather (Task 10)
# ---------------------------------------------------------------------------


@pytest.fixture
async def active_event(app_s):
    """Creates an ACTIVE event owned by W0NC and returns (app, settings, event_id)."""
    app, settings = app_s
    async with _c(app, settings, "W0NC") as c:
        r = await c.post("/api/events", json={"name": "Task10", "event_type": "emergency"})
        assert r.status_code == 201
        eid = r.json()["id"]
        r2 = await c.post(f"/api/events/{eid}/activate")
        assert r2.status_code == 200
    return app, settings, eid


@pytest.mark.asyncio
async def test_control_can_add_post(active_event):
    app, settings, eid = active_event
    async with _c(app, settings, "W0NC") as c:
        r = await c.post(f"/api/events/{eid}/posts", json={"name": "Alpha"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Alpha" and body["event_id"] == eid


@pytest.mark.asyncio
async def test_non_control_cannot_add_post(active_event):
    app, settings, eid = active_event
    async with _c(app, settings, "W0OUT") as c:
        r = await c.post(f"/api/events/{eid}/posts", json={"name": "Beta"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_control_can_update_and_delete_post(active_event):
    app, settings, eid = active_event
    async with _c(app, settings, "W0NC") as c:
        post_id = (await c.post(f"/api/events/{eid}/posts", json={"name": "Gamma"})).json()["id"]
        r_patch = await c.patch(f"/api/events/{eid}/posts/{post_id}", json={"name": "Gamma2"})
        assert r_patch.status_code == 200
        assert r_patch.json()["name"] == "Gamma2"
        r_del = await c.delete(f"/api/events/{eid}/posts/{post_id}")
        assert r_del.status_code == 204


@pytest.mark.asyncio
async def test_control_can_check_in_participant(active_event):
    app, settings, eid = active_event
    async with _c(app, settings, "W0NC") as c:
        r = await c.post(f"/api/events/{eid}/participants", json={"callsign": "KE0ABC"})
    assert r.status_code == 201
    body = r.json()
    assert body["callsign"] == "KE0ABC" and body["event_id"] == eid


@pytest.mark.asyncio
async def test_non_control_cannot_check_in(active_event):
    app, settings, eid = active_event
    async with _c(app, settings, "W0OUT") as c:
        r = await c.post(f"/api/events/{eid}/participants", json={"callsign": "KE0XYZ"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_control_can_add_note(active_event):
    app, settings, eid = active_event
    async with _c(app, settings, "W0NC") as c:
        r = await c.post(f"/api/events/{eid}/log", json={"message": "All is well"})
    assert r.status_code == 201
    body = r.json()
    assert body["message"] == "All is well"


@pytest.mark.asyncio
async def test_non_control_cannot_add_note(active_event):
    app, settings, eid = active_event
    async with _c(app, settings, "W0OUT") as c:
        r = await c.post(f"/api/events/{eid}/log", json={"message": "Sneaky note"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_updates_readable_by_control(active_event):
    app, settings, eid = active_event
    async with _c(app, settings, "W0NC") as c:
        r = await c.get(f"/api/events/{eid}/updates")
    assert r.status_code == 200
    body = r.json()
    assert "event" in body and "log" in body


@pytest.mark.asyncio
async def test_report_readable_by_control(active_event):
    app, settings, eid = active_event
    async with _c(app, settings, "W0NC") as c:
        r = await c.get(f"/api/events/{eid}/report")
    assert r.status_code == 200
    assert "participants" in r.json()


@pytest.mark.asyncio
async def test_positions_disabled_shape(active_event):
    """When APRS manager has no state, /positions returns the disabled shape."""
    app, settings, eid = active_event
    async with _c(app, settings, "W0NC") as c:
        r = await c.get(f"/api/events/{eid}/positions")
    assert r.status_code == 200
    body = r.json()
    assert body["aprs_status"] == "disabled" and body["stations"] == []


@pytest.mark.asyncio
async def test_weather_returns_status(active_event):
    app, settings, eid = active_event
    async with _c(app, settings, "W0NC") as c:
        r = await c.get(f"/api/events/{eid}/weather")
    assert r.status_code == 200
    assert "status" in r.json()


# --- Public token: anonymous access to positions + weather ---

@pytest.fixture
async def public_event(app_s):
    """Creates an ACTIVE public event and returns (app, settings, event_id, public_token)."""
    from backend.modules.events.service import set_visibility
    app, settings = app_s
    async with _c(app, settings, "W0NC") as c:
        r = await c.post("/api/events", json={"name": "Public", "event_type": "emergency"})
        eid = r.json()["id"]
        pub_token = r.json()["public_token"]
        await c.post(f"/api/events/{eid}/activate")
    # flip visibility directly via service so we don't need an API route for it yet
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.modules.events.models import Event
    with app.state.session_factory() as db:
        ev = db.get(Event, eid)
        set_visibility(db, ev, "public")
    return app, settings, eid, pub_token


@pytest.mark.asyncio
async def test_public_positions_anonymous_with_token(public_event):
    app, settings, eid, tok = public_event
    async with _c(app, settings) as c:  # no callsign = anonymous
        r = await c.get(f"/api/events/{eid}/positions?token={tok}")
    assert r.status_code == 200
    assert r.json()["aprs_status"] == "disabled"


@pytest.mark.asyncio
async def test_public_weather_anonymous_with_token(public_event):
    app, settings, eid, tok = public_event
    async with _c(app, settings) as c:
        r = await c.get(f"/api/events/{eid}/weather?token={tok}")
    assert r.status_code == 200
    assert "status" in r.json()


@pytest.mark.asyncio
async def test_private_positions_anonymous_returns_404(active_event):
    """A private event's /positions is 404 for anonymous callers (no existence signal)."""
    app, settings, eid = active_event
    async with _c(app, settings) as c:
        r = await c.get(f"/api/events/{eid}/positions")
    assert r.status_code == 404
