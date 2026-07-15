import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.events.models import Event, EventType
from backend.modules.nets.models import Net, NetMembership, NetRole
from tests.conftest import make_test_net, make_test_token

NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/events"


def test_aprslib_importable():
    import aprslib

    assert aprslib.passcode("N0CALL") == 13023


def test_event_aprs_defaults():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        net = make_test_net(db)
        event = Event(net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE")
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
def test_settings():
    return Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)


@pytest.fixture
def db_setup():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        nc = User(callsign="W0NC", oidc_subject="auth0|nc", name="NC")
        net = Net(slug=NET_SLUG, name="Test Net", is_public=False)
        session.add_all([nc, net])
        session.flush()
        session.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
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


class TestAprsSettingsPatch:
    async def test_response_includes_aprs_fields(self, nc_client):
        resp = await nc_client.post(BASE, json={"name": "E", "event_type": "emergency", "activate": True})
        body = resp.json()
        assert body["aprs_other_stations"] is False
        assert body["aprs_beacon_posts"] is False
        assert body["aprs_range_km"] is None

    async def test_patch_aprs_fields(self, nc_client):
        event_id = (await nc_client.post(
            BASE, json={"name": "E", "event_type": "emergency", "activate": True}
        )).json()["id"]
        resp = await nc_client.patch(f"{BASE}/{event_id}", json={
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

    async def test_patch_partial_leaves_others(self, nc_client):
        event_id = (await nc_client.post(
            BASE, json={"name": "E", "event_type": "emergency", "activate": True}
        )).json()["id"]
        await nc_client.patch(f"{BASE}/{event_id}", json={"aprs_beacon_posts": True})
        resp = await nc_client.patch(f"{BASE}/{event_id}", json={"name": "Renamed"})
        assert resp.json()["aprs_beacon_posts"] is True
