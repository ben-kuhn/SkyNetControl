import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.nets.models import Net, NetMembership, NetRole
from tests.conftest import make_test_token

NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/events"


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)


@pytest.fixture
def db_setup():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
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


@pytest.fixture
async def active_event(nc_client):
    resp = await nc_client.post(BASE, json={"name": "SKYWARN", "event_type": "emergency", "activate": True})
    return resp.json()["id"]


class TestParticipantRoutes:
    async def test_check_in(self, nc_client, active_event):
        resp = await nc_client.post(f"{BASE}/{active_event}/participants", json={"callsign": "ke0xyz"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["callsign"] == "KE0XYZ"
        assert body["current_status"] == "checked_in"

    async def test_duplicate_check_in_409(self, nc_client, active_event):
        await nc_client.post(f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"})
        resp = await nc_client.post(f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"})
        assert resp.status_code == 409

    async def test_status_change_and_invalid_transition(self, nc_client, active_event):
        pid = (await nc_client.post(
            f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"}
        )).json()["id"]
        resp = await nc_client.patch(
            f"{BASE}/{active_event}/participants/{pid}", json={"status": "checked_out"}
        )
        assert resp.status_code == 200
        assert resp.json()["current_status"] == "checked_out"
        resp = await nc_client.patch(
            f"{BASE}/{active_event}/participants/{pid}", json={"status": "at_post"}
        )
        assert resp.status_code == 422

    async def test_unassign_post_via_null(self, nc_client, active_event):
        post_id = (await nc_client.post(
            f"{BASE}/{active_event}/posts", json={"name": "EOC"}
        )).json()["id"]
        pid = (await nc_client.post(
            f"{BASE}/{active_event}/participants",
            json={"callsign": "KE0XYZ", "post_id": post_id},
        )).json()["id"]
        resp = await nc_client.patch(
            f"{BASE}/{active_event}/participants/{pid}", json={"post_id": None}
        )
        assert resp.status_code == 200
        assert resp.json()["post_id"] is None

    async def test_write_on_closed_event_409(self, nc_client, active_event):
        await nc_client.post(f"{BASE}/{active_event}/close")
        resp = await nc_client.post(f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"})
        assert resp.status_code == 409

    async def test_viewer_cannot_write(self, viewer_client, active_event):
        resp = await viewer_client.post(f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"})
        assert resp.status_code == 403


class TestLogRoutes:
    async def test_add_note(self, nc_client, active_event):
        resp = await nc_client.post(f"{BASE}/{active_event}/log", json={"message": "Course clear"})
        assert resp.status_code == 201
        assert resp.json()["entry_type"] == "note"

    async def test_participant_note_with_pin(self, nc_client, active_event):
        await nc_client.post(f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"})
        resp = await nc_client.post(
            f"{BASE}/{active_event}/log",
            json={"message": "Medic", "callsign": "ke0xyz", "pinned": True},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["entry_type"] == "participant_note"
        assert body["callsign"] == "KE0XYZ"
        assert body["pinned"] is True

    async def test_pin_toggle(self, nc_client, active_event):
        entry_id = (await nc_client.post(
            f"{BASE}/{active_event}/log", json={"message": "x"}
        )).json()["id"]
        resp = await nc_client.patch(f"{BASE}/{active_event}/log/{entry_id}", json={"pinned": True})
        assert resp.status_code == 200
        assert resp.json()["pinned"] is True


class TestUpdatesRoute:
    async def test_cursor_semantics(self, nc_client, viewer_client, active_event):
        # since=0 → full log (activation entry)
        resp = await viewer_client.get(f"{BASE}/{active_event}/updates", params={"since": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert body["latest_seq"] == 1
        assert len(body["log"]) == 1

        await nc_client.post(f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"})

        # catch-up from previous cursor → only the new entry
        resp = await viewer_client.get(f"{BASE}/{active_event}/updates", params={"since": 1})
        body = resp.json()
        assert body["latest_seq"] == 2
        assert len(body["log"]) == 1
        assert body["log"][0]["seq"] == 2
        # state always included whole
        assert len(body["participants"]) == 1

        # up-to-date cursor → empty delta
        resp = await viewer_client.get(f"{BASE}/{active_event}/updates", params={"since": 2})
        assert resp.json()["log"] == []


class TestReportRoute:
    async def test_report(self, nc_client, viewer_client, active_event):
        pid = (await nc_client.post(
            f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"}
        )).json()["id"]
        await nc_client.patch(
            f"{BASE}/{active_event}/participants/{pid}", json={"status": "checked_out"}
        )
        resp = await viewer_client.get(f"{BASE}/{active_event}/report")
        assert resp.status_code == 200
        participants = resp.json()["participants"]
        assert len(participants) == 1
        assert participants[0]["callsign"] == "KE0XYZ"
        assert len(participants[0]["stints"]) == 1
        assert participants[0]["stints"][0]["end"] is not None
