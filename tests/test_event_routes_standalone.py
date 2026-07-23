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


# ---------------------------------------------------------------------------
# Owner-only routes: operators, visibility, token rotation, transfer (Task 12)
# ---------------------------------------------------------------------------


@pytest.fixture
async def owner_event(app_s):
    """Creates an event owned by W0NC; W0COP is a co-operator (control but not owner).
    Returns (app, settings, event_id).
    """
    app, settings = app_s
    async with _c(app, settings, "W0NC") as c:
        r = await c.post("/api/events", json={"name": "OwnerTest", "event_type": "emergency"})
        assert r.status_code == 201
        eid = r.json()["id"]
        # Add W0COP as a co-operator via service directly so we can test the co-op 403 path
    with app.state.session_factory() as db:
        from backend.modules.events.models import Event
        from backend.modules.events.service import add_operator
        ev = db.get(Event, eid)
        add_operator(db, ev, "W0COP", added_by="W0NC")
    # Also add W0COP as an approved User so auth resolves
    with app.state.session_factory() as db:
        from backend.auth.models import User as UserModel
        existing = db.get(UserModel, "W0COP")
        if existing is None:
            db.add(UserModel(callsign="W0COP", oidc_subject="x|cop", name="Co-op"))
            db.commit()
    return app, settings, eid


@pytest.mark.asyncio
async def test_owner_can_add_operator(owner_event):
    app, settings, eid = owner_event
    async with _c(app, settings, "W0NC") as c:
        r = await c.post(f"/api/events/{eid}/operators", json={"callsign": "KE0NEW"})
    assert r.status_code == 201
    assert "KE0NEW" in r.json()["operators"]


@pytest.mark.asyncio
async def test_owner_can_remove_operator(owner_event):
    app, settings, eid = owner_event
    async with _c(app, settings, "W0NC") as c:
        # W0COP was added by the fixture; remove them
        r = await c.delete(f"/api/events/{eid}/operators/W0COP")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_owner_can_set_visibility(owner_event):
    app, settings, eid = owner_event
    async with _c(app, settings, "W0NC") as c:
        r = await c.patch(f"/api/events/{eid}/visibility", json={"visibility": "public"})
    assert r.status_code == 200
    assert r.json()["visibility"] == "public"


@pytest.mark.asyncio
async def test_visibility_invalid_returns_422(owner_event):
    app, settings, eid = owner_event
    async with _c(app, settings, "W0NC") as c:
        r = await c.patch(f"/api/events/{eid}/visibility", json={"visibility": "secret"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_owner_can_rotate_token_and_old_token_revoked(owner_event):
    """After rotation the old public_token no longer grants anonymous access."""
    app, settings, eid = owner_event
    # First make the event public and active so anonymous read is meaningful
    async with _c(app, settings, "W0NC") as c:
        await c.post(f"/api/events/{eid}/activate")
        await c.patch(f"/api/events/{eid}/visibility", json={"visibility": "public"})
        # Fetch the current token
        detail = await c.get(f"/api/events/{eid}")
        old_token = detail.json()["event"]["public_token"]
        # Rotate it
        r = await c.post(f"/api/events/{eid}/token/rotate")
        assert r.status_code == 200
        new_token = r.json()["public_token"]
    assert new_token != old_token
    # Old token: anonymous positions endpoint should return 404 (token no longer valid)
    async with _c(app, settings) as c:
        r_old = await c.get(f"/api/events/{eid}/positions?token={old_token}")
        assert r_old.status_code == 404
    # New token works
    async with _c(app, settings) as c:
        r_new = await c.get(f"/api/events/{eid}/positions?token={new_token}")
        assert r_new.status_code == 200


@pytest.mark.asyncio
async def test_owner_can_transfer_ownership(owner_event):
    """After transfer: new owner has control; old owner does not."""
    app, settings, eid = owner_event
    # Add W0OUT as approved user if needed
    with app.state.session_factory() as db:
        from backend.auth.models import User as UserModel
        existing = db.get(UserModel, "W0OUT")
        if existing is None:
            db.add(UserModel(callsign="W0OUT", oidc_subject="x|o", name="Out"))
            db.commit()
    async with _c(app, settings, "W0NC") as c:
        r = await c.post(f"/api/events/{eid}/transfer", json={"callsign": "W0OUT"})
    assert r.status_code == 200
    assert r.json()["owner"] == "W0OUT"
    # New owner W0OUT can now access the event with control
    async with _c(app, settings, "W0OUT") as c:
        r2 = await c.get(f"/api/events/{eid}")
        assert r2.status_code == 200
        assert r2.json()["event"]["is_control"] is True
    # Old owner W0NC no longer has control (not in operators list, not owner)
    async with _c(app, settings, "W0NC") as c:
        r3 = await c.get(f"/api/events/{eid}")
        assert r3.status_code == 403


@pytest.mark.asyncio
async def test_co_operator_403_on_add_operator(owner_event):
    app, settings, eid = owner_event
    async with _c(app, settings, "W0COP") as c:
        r = await c.post(f"/api/events/{eid}/operators", json={"callsign": "KE0BAD"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_co_operator_403_on_remove_operator(owner_event):
    app, settings, eid = owner_event
    async with _c(app, settings, "W0COP") as c:
        r = await c.delete(f"/api/events/{eid}/operators/W0COP")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_co_operator_403_on_visibility(owner_event):
    app, settings, eid = owner_event
    async with _c(app, settings, "W0COP") as c:
        r = await c.patch(f"/api/events/{eid}/visibility", json={"visibility": "public"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_co_operator_403_on_rotate_token(owner_event):
    app, settings, eid = owner_event
    async with _c(app, settings, "W0COP") as c:
        r = await c.post(f"/api/events/{eid}/token/rotate")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_co_operator_403_on_transfer(owner_event):
    app, settings, eid = owner_event
    async with _c(app, settings, "W0COP") as c:
        r = await c.post(f"/api/events/{eid}/transfer", json={"callsign": "W0COP"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Fix 1: ORM cascade for EventOperator + EventConfig; PAT session NULL out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_cascades_operator_config_and_nulls_pat_session(app_s):
    from datetime import datetime, timezone
    from backend.modules.events.models import Event, EventOperator, EventConfig, EventType, EventStatus
    from backend.integrations.winlink.models import PatConnectionSession, PatSessionStatus

    app, settings = app_s

    # Create event via service so public_token etc. are set
    async with _c(app, settings, "W0NC") as c:
        r = await c.post("/api/events", json={"name": "CascadeTest", "event_type": "emergency"})
        assert r.status_code == 201
        eid = r.json()["id"]

    # Directly add an EventOperator and EventConfig row
    with app.state.session_factory() as db:
        db.add(EventOperator(event_id=eid, callsign="W0OP", added_by="W0NC",
                             added_at=datetime.now(timezone.utc)))
        db.add(EventConfig(event_id=eid, key="net_address", value="W0NE@winlink.org"))
        # Add a PatConnectionSession referencing this event
        from backend.modules.nets.models import Net
        net = db.query(Net).first()
        net_id = net.id if net else None
        # Use a bare engine to get nets.id (may not exist); create dummy session
        pat = PatConnectionSession(
            net_id=None,
            event_id=eid,
            connect_url="http://localhost:8080",
            method_label="test",
            status=PatSessionStatus.COMPLETED,
            actor="W0NC",
            started_at=datetime.now(timezone.utc),
        )
        db.add(pat)
        db.commit()
        pat_id = pat.id

    # DELETE the event via the API
    async with _c(app, settings, "W0NC") as c:
        r = await c.delete(f"/api/events/{eid}")
        assert r.status_code == 204

    # Assert operator row and config row are GONE
    with app.state.session_factory() as db:
        op_rows = db.query(EventOperator).filter(EventOperator.event_id == eid).all()
        cfg_rows = db.query(EventConfig).filter(EventConfig.event_id == eid).all()
        assert op_rows == [], f"Expected no operator rows, got {op_rows}"
        assert cfg_rows == [], f"Expected no config rows, got {cfg_rows}"

        # PAT session still exists but event_id is NULL
        pat_row = db.get(PatConnectionSession, pat_id)
        assert pat_row is not None, "PAT session should still exist"
        assert pat_row.event_id is None, f"Expected event_id=None, got {pat_row.event_id}"
