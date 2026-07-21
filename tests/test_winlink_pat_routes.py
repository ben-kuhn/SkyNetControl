import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import create_app
from backend.config import Settings
from backend.db.base import Base
from backend.auth.models import User
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.modules.nets.config_service import set_net_config
from backend.integrations.winlink import pat_session
from tests.conftest import make_test_token

BASE = "/api/nets/t"


@pytest.fixture(autouse=True)
def _reset_engine():
    pat_session.engine = pat_session.PatSessionEngine()
    yield


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)


@pytest.fixture
def db_setup():
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    with factory() as db:
        db.add(User(callsign="W0NC", oidc_subject="x|nc", name="NC"))
        net = Net(slug="t", name="T")
        db.add(net)
        db.flush()
        db.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        set_net_config(db, net.id, "pat_transport_enabled", "true")
        set_net_config(db, net.id, "pat_http_base_url", "http://pat.test")
        db.commit()
        yield {"engine": eng, "factory": factory, "net_id": net.id}
    eng.dispose()


@pytest.fixture
def app(test_settings, db_setup):
    application = create_app(settings=test_settings)
    application.state.engine = db_setup["engine"]
    application.state.session_factory = db_setup["factory"]
    return application


async def _nc_client(app, test_settings):
    token = make_test_token("W0NC", test_settings)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       cookies={"access_token": token})


async def test_connect_options_requires_nc(app, test_settings, monkeypatch):
    monkeypatch.setattr("backend.modules.events.pat_routes.build_connect_options",
                        lambda client: {"aliases": [{"name": "gw1", "url": "telnet:///"}], "gateways": []})
    async with await _nc_client(app, test_settings) as c:
        r = await c.get(f"{BASE}/pat/connect-options")
    assert r.status_code == 200
    assert r.json()["aliases"][0]["name"] == "gw1"


async def test_connect_starts_session_and_second_is_409(app, test_settings, monkeypatch):
    from backend.integrations.winlink.pat_client import PatClient

    calls = {"n": 0}

    async def start(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return 7
        raise pat_session.SessionBusy("busy")

    import backend.modules.events.pat_routes as pat_routes_mod
    monkeypatch.setattr(pat_routes_mod.engine, "start", start)
    monkeypatch.setattr(PatClient, "connect_aliases", lambda self: {"gw1": "telnet:///"})
    monkeypatch.setattr("backend.modules.events.pat_routes.resolve_connect_url",
                        lambda body, aliases: ("telnet:///", "alias: gw1"))
    async with await _nc_client(app, test_settings) as c:
        r1 = await c.post(f"{BASE}/pat/connect", json={"alias": "gw1"})
        r2 = await c.post(f"{BASE}/pat/connect", json={"alias": "gw1"})
    assert r1.status_code == 201 and r1.json()["session_id"] == 7
    assert r2.status_code == 409


async def test_test_route_reports_ok(app, test_settings, monkeypatch):
    monkeypatch.setattr("backend.modules.events.pat_routes._probe_status",
                        lambda client: True)
    async with await _nc_client(app, test_settings) as c:
        r = await c.post(f"{BASE}/pat/test")
    assert r.status_code == 200 and r.json()["ok"] is True
