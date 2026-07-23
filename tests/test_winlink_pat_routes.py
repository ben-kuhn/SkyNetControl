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


# ---------------------------------------------------------------------------
# Net-scoped PAT routes
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Event-scoped PAT routes — /api/events/{event_id}/pat/…
# ---------------------------------------------------------------------------

@pytest.fixture
def event_db_setup():
    """DB with a user + active event with PAT transport configured."""
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    with factory() as db:
        db.add(User(callsign="W0NC", oidc_subject="x|nc", name="NC"))
        db.commit()
        yield {"engine": eng, "factory": factory}
    eng.dispose()


@pytest.fixture
def event_app(test_settings, event_db_setup):
    application = create_app(settings=test_settings)
    application.state.engine = event_db_setup["engine"]
    application.state.session_factory = event_db_setup["factory"]
    return application


async def _event_nc_client(app, test_settings):
    token = make_test_token("W0NC", test_settings)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       cookies={"access_token": token})


@pytest.fixture
async def active_event_id(event_app, test_settings, event_db_setup):
    """Create and activate an event, set pat config."""
    from backend.modules.events.event_config_service import set_event_config
    async with await _event_nc_client(event_app, test_settings) as c:
        resp = await c.post("/api/events", json={"name": "PAT Test Event", "event_type": "emergency"})
        assert resp.status_code == 201
        event_id = resp.json()["id"]
        resp2 = await c.post(f"/api/events/{event_id}/activate")
        assert resp2.status_code == 200
    with event_db_setup["factory"]() as db:
        set_event_config(db, event_id, "pat_transport_enabled", "true")
        set_event_config(db, event_id, "pat_http_base_url", "http://pat.test")
    return event_id


async def test_event_pat_connect_options(event_app, test_settings, active_event_id, monkeypatch):
    """Event-scoped connect-options route returns aliases from PAT."""
    import backend.modules.events.pat_routes as pat_routes_mod
    monkeypatch.setattr(pat_routes_mod, "build_connect_options",
                        lambda client: {"aliases": [{"name": "gw1", "url": "telnet:///"}], "gateways": []})
    # Patch build_pat_client so no real HTTP call is made
    import backend.integrations.winlink.pat_config as pc
    monkeypatch.setattr(pc, "build_pat_client", lambda cfg: object())

    async with await _event_nc_client(event_app, test_settings) as c:
        r = await c.get(f"/api/events/{active_event_id}/pat/connect-options")
    assert r.status_code == 200
    assert r.json()["aliases"][0]["name"] == "gw1"


async def test_event_pat_connect_starts_event_scoped_session(
    event_app, test_settings, active_event_id, monkeypatch
):
    """Event PAT connect must create a session with net_id=None and event_id set."""
    from backend.integrations.winlink.pat_client import PatClient
    import backend.modules.events.pat_routes as pat_routes_mod
    import backend.integrations.winlink.pat_config as pc

    captured = {}

    async def fake_start(session_factory, *, net_id, event_id, actor, connect_url, method_label, client):
        captured.update({"net_id": net_id, "event_id": event_id})
        return 42

    monkeypatch.setattr(pat_routes_mod.engine, "start", fake_start)
    monkeypatch.setattr(PatClient, "connect_aliases", lambda self: {"gw1": "telnet:///"})
    monkeypatch.setattr(pat_routes_mod, "resolve_connect_url",
                        lambda body, aliases: ("telnet:///", "alias: gw1"))
    monkeypatch.setattr(pc, "build_pat_client", lambda cfg: PatClient.__new__(PatClient))

    async with await _event_nc_client(event_app, test_settings) as c:
        r = await c.post(f"/api/events/{active_event_id}/pat/connect", json={"alias": "gw1"})
    assert r.status_code == 201
    assert r.json()["session_id"] == 42
    # Key invariant: event-scoped session has net_id=None, event_id=active_event_id
    assert captured["net_id"] is None, f"Expected net_id=None, got {captured['net_id']!r}"
    assert captured["event_id"] == active_event_id, (
        f"Expected event_id={active_event_id}, got {captured['event_id']!r}"
    )


async def test_event_pat_test_route(event_app, test_settings, active_event_id, monkeypatch):
    """Event-scoped PAT test route reports ok."""
    import backend.modules.events.pat_routes as pat_routes_mod
    monkeypatch.setattr(pat_routes_mod, "_probe_status", lambda client: True)
    import backend.integrations.winlink.pat_config as pc
    monkeypatch.setattr(pc, "build_pat_client", lambda cfg: object())

    async with await _event_nc_client(event_app, test_settings) as c:
        r = await c.post(f"/api/events/{active_event_id}/pat/test")
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_event_pat_session_status_and_abort(
    event_app, test_settings, active_event_id, monkeypatch, event_db_setup
):
    """Session status route returns session data; session scoped to event."""
    from datetime import datetime, timezone
    from backend.integrations.winlink.models import PatConnectionSession, PatSessionStatus
    import backend.integrations.winlink.pat_config as pc
    import backend.modules.events.pat_routes as pat_routes_mod

    # Seed a session belonging to the event
    with event_db_setup["factory"]() as db:
        s = PatConnectionSession(
            net_id=None, event_id=active_event_id,
            connect_url="telnet:///", method_label="test",
            status=PatSessionStatus.COMPLETED,
            sent_count=0, received_count=0, events=[],
            actor="W0NC", started_at=datetime.now(timezone.utc),
        )
        db.add(s)
        db.commit()
        session_id = s.id

    monkeypatch.setattr(pc, "build_pat_client", lambda cfg: object())
    monkeypatch.setattr(pat_routes_mod.engine, "abort", lambda sf, sid, client: None)

    async with await _event_nc_client(event_app, test_settings) as c:
        r = await c.get(f"/api/events/{active_event_id}/pat/sessions/{session_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
