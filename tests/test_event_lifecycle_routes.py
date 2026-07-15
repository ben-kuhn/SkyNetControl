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
        net_control = User(callsign="W0NC", oidc_subject="auth0|nc", name="Net Control")
        viewer = User(callsign="KD0TST", oidc_subject="auth0|viewer", name="Viewer")
        outsider = User(callsign="W0OUT", oidc_subject="auth0|out", name="Outsider")
        net = Net(slug=NET_SLUG, name="Test Net", is_public=False)
        session.add_all([net_control, viewer, outsider, net])
        session.flush()
        session.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        session.add(NetMembership(user_callsign="KD0TST", net_id=net.id, role=NetRole.VIEWER))
        session.commit()
        yield {"engine": engine, "factory": factory, "net": net}
    engine.dispose()


@pytest.fixture
def app(test_settings, db_setup):
    from backend.app import create_app

    application = create_app(settings=test_settings)
    application.state.engine = db_setup["engine"]
    application.state.session_factory = db_setup["factory"]
    return application


def _client(app, test_settings, callsign, **kwargs):
    token = make_test_token(callsign, test_settings, **kwargs)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token})


@pytest.fixture
async def nc_client(app, test_settings):
    async with _client(app, test_settings, "W0NC") as c:
        yield c


@pytest.fixture
async def viewer_client(app, test_settings):
    async with _client(app, test_settings, "KD0TST") as c:
        yield c


@pytest.fixture
async def outsider_client(app, test_settings):
    async with _client(app, test_settings, "W0OUT") as c:
        yield c


DRAFT_BODY = {"name": "Marathon", "event_type": "public_service"}
ACTIVE_BODY = {"name": "Tornado", "event_type": "emergency", "activate": True}


class TestLifecycleRoutes:
    async def test_create_draft(self, nc_client):
        resp = await nc_client.post(BASE, json=DRAFT_BODY)
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "draft"
        assert body["event_type"] == "public_service"
        assert body["created_by"] == "W0NC"

    async def test_create_and_activate(self, nc_client):
        resp = await nc_client.post(BASE, json=ACTIVE_BODY)
        assert resp.status_code == 201
        assert resp.json()["status"] == "active"

    async def test_list_events(self, nc_client):
        await nc_client.post(BASE, json=DRAFT_BODY)
        await nc_client.post(BASE, json=ACTIVE_BODY)
        resp = await nc_client.get(BASE)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_get_snapshot(self, nc_client):
        event_id = (await nc_client.post(BASE, json=ACTIVE_BODY)).json()["id"]
        resp = await nc_client.get(f"{BASE}/{event_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["event"]["id"] == event_id
        assert body["posts"] == []
        assert body["participants"] == []
        assert len(body["log"]) == 1  # "Event activated"

    async def test_activate_close_reopen(self, nc_client):
        event_id = (await nc_client.post(BASE, json=DRAFT_BODY)).json()["id"]
        assert (await nc_client.post(f"{BASE}/{event_id}/activate")).status_code == 200
        assert (await nc_client.post(f"{BASE}/{event_id}/activate")).status_code == 409
        assert (await nc_client.post(f"{BASE}/{event_id}/close")).status_code == 200
        assert (await nc_client.post(f"{BASE}/{event_id}/close")).status_code == 409
        resp = await nc_client.post(f"{BASE}/{event_id}/reopen")
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    async def test_patch_event(self, nc_client):
        event_id = (await nc_client.post(BASE, json=DRAFT_BODY)).json()["id"]
        resp = await nc_client.patch(f"{BASE}/{event_id}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    async def test_patch_closed_event_409(self, nc_client):
        event_id = (await nc_client.post(BASE, json=ACTIVE_BODY)).json()["id"]
        await nc_client.post(f"{BASE}/{event_id}/close")
        resp = await nc_client.patch(f"{BASE}/{event_id}", json={"name": "Nope"})
        assert resp.status_code == 409

    async def test_missing_event_404(self, nc_client):
        assert (await nc_client.get(f"{BASE}/9999")).status_code == 404

    async def test_patch_missing_event_404(self, nc_client):
        resp = await nc_client.patch(f"{BASE}/9999", json={"name": "X"})
        assert resp.status_code == 404


class TestPostRoutes:
    async def test_post_crud(self, nc_client):
        event_id = (await nc_client.post(BASE, json=ACTIVE_BODY)).json()["id"]
        resp = await nc_client.post(f"{BASE}/{event_id}/posts", json={"name": "EOC", "lat": 39.1, "lon": -94.6})
        assert resp.status_code == 201
        post_id = resp.json()["id"]

        dup = await nc_client.post(f"{BASE}/{event_id}/posts", json={"name": "EOC"})
        assert dup.status_code == 409

        resp = await nc_client.patch(f"{BASE}/{event_id}/posts/{post_id}", json={"name": "EOC Main"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "EOC Main"

        resp = await nc_client.delete(f"{BASE}/{event_id}/posts/{post_id}")
        assert resp.status_code == 204


class TestPermissions:
    async def test_viewer_can_read(self, nc_client, viewer_client):
        event_id = (await nc_client.post(BASE, json=ACTIVE_BODY)).json()["id"]
        assert (await viewer_client.get(BASE)).status_code == 200
        assert (await viewer_client.get(f"{BASE}/{event_id}")).status_code == 200

    async def test_viewer_cannot_write(self, nc_client, viewer_client):
        event_id = (await nc_client.post(BASE, json=ACTIVE_BODY)).json()["id"]
        assert (await viewer_client.post(BASE, json=DRAFT_BODY)).status_code == 403
        assert (await viewer_client.post(f"{BASE}/{event_id}/close")).status_code == 403
        assert (await viewer_client.post(f"{BASE}/{event_id}/posts", json={"name": "X"})).status_code == 403

    async def test_outsider_denied(self, outsider_client):
        assert (await outsider_client.get(BASE)).status_code == 403

    async def test_anonymous_denied(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as anon:
            assert (await anon.get(BASE)).status_code == 401
