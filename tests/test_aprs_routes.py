import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.integrations.aprs import manager
from backend.integrations.aprs.store import EventPositionStore
from backend.modules.nets.models import Net, NetMembership, NetRole
from tests.conftest import make_test_token

NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/events"


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)


@pytest.fixture
def db_setup():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        nc = User(callsign="W0NC", oidc_subject="auth0|nc", name="NC")
        viewer = User(callsign="KD0TST", oidc_subject="auth0|v", name="V")
        net = Net(slug=NET_SLUG, name="Test Net", is_public=False)
        session.add_all([nc, viewer, net])
        session.flush()
        session.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        session.add(NetMembership(user_callsign="KD0TST", net_id=net.id, role=NetRole.VIEWER))
        session.commit()
        yield {"engine": engine, "factory": factory}
    engine.dispose()


@pytest.fixture
def app(test_settings, db_setup):
    from backend.app import create_app

    application = create_app(settings=test_settings)
    application.state.engine = db_setup["engine"]
    application.state.session_factory = db_setup["factory"]
    return application


@pytest.fixture
async def nc_client(app, test_settings):
    token = make_test_token("W0NC", test_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token}) as c:
        yield c


@pytest.fixture
async def viewer_client(app, test_settings):
    token = make_test_token("KD0TST", test_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token}) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_states():
    manager._states.clear()
    yield
    manager._states.clear()


@pytest.fixture
async def active_event(nc_client):
    resp = await nc_client.post(BASE, json={"name": "E", "event_type": "emergency", "activate": True})
    return resp.json()["id"]


class TestPositionsRoute:
    async def test_disabled_when_no_client(self, viewer_client, active_event):
        resp = await viewer_client.get(f"{BASE}/{active_event}/positions")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "stations": [], "latest_pos_seq": 0,
            "aprs_status": "disabled", "aprs_status_detail": "", "objects": [],
        }

    async def test_snapshot_from_running_state(self, viewer_client, active_event, db_setup):
        state = manager.AprsClientState(event_id=active_event, session_factory=db_setup["factory"])
        state.status = "connected"
        state.store.add_point("KE0XYZ-9", 39.0, -94.0, kind="participant", callsign="KE0XYZ")
        state.objects_by_post = {1: "RESTSTOP3"}
        manager._states[active_event] = state

        resp = await viewer_client.get(f"{BASE}/{active_event}/positions", params={"since": 0})
        body = resp.json()
        assert body["aprs_status"] == "connected"
        assert body["latest_pos_seq"] == 1
        assert body["stations"][0]["station_id"] == "KE0XYZ-9"
        assert body["objects"] == [{"post_id": 1, "name": "RESTSTOP3"}]

        # Cursor: no new points, but roster still complete
        resp = await viewer_client.get(f"{BASE}/{active_event}/positions", params={"since": 1})
        body = resp.json()
        assert body["stations"][0]["points"] == []

    async def test_missing_event_404(self, viewer_client):
        assert (await viewer_client.get(f"{BASE}/9999/positions")).status_code == 404

    async def test_anonymous_401(self, app, active_event):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as anon:
            assert (await anon.get(f"{BASE}/{active_event}/positions")).status_code == 401


class TestLifecycleHooks:
    async def test_activate_calls_ensure_started(self, nc_client, monkeypatch):
        calls = []
        monkeypatch.setattr(manager, "ensure_started", lambda sf, eid: calls.append(("start", eid)))
        resp = await nc_client.post(BASE, json={"name": "E", "event_type": "emergency", "activate": True})
        event_id = resp.json()["id"]
        assert ("start", event_id) in calls

    async def test_close_calls_stop_and_reopen_restarts(self, nc_client, monkeypatch):
        calls = []
        monkeypatch.setattr(manager, "ensure_started", lambda sf, eid: calls.append(("start", eid)))
        monkeypatch.setattr(manager, "stop", lambda eid: calls.append(("stop", eid)))
        event_id = (await nc_client.post(
            BASE, json={"name": "E", "event_type": "emergency", "activate": True}
        )).json()["id"]
        await nc_client.post(f"{BASE}/{event_id}/close")
        assert ("stop", event_id) in calls
        await nc_client.post(f"{BASE}/{event_id}/reopen")
        assert calls.count(("start", event_id)) == 2

    async def test_checkin_and_posts_and_patch_nudge(self, nc_client, monkeypatch):
        nudges = []
        monkeypatch.setattr(manager, "ensure_started", lambda sf, eid: None)
        monkeypatch.setattr(manager, "nudge", lambda eid: nudges.append(eid))
        event_id = (await nc_client.post(
            BASE, json={"name": "E", "event_type": "emergency", "activate": True}
        )).json()["id"]
        await nc_client.post(f"{BASE}/{event_id}/participants", json={"callsign": "KE0XYZ"})
        post_id = (await nc_client.post(
            f"{BASE}/{event_id}/posts", json={"name": "EOC", "lat": 39.0, "lon": -94.0}
        )).json()["id"]
        await nc_client.patch(f"{BASE}/{event_id}/posts/{post_id}", json={"lat": 39.1})
        await nc_client.patch(f"{BASE}/{event_id}", json={"aprs_beacon_posts": True})
        await nc_client.delete(f"{BASE}/{event_id}/posts/{post_id}")
        assert nudges.count(event_id) >= 5
