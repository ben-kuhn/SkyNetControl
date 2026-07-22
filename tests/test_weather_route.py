import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI

from backend.db.base import Base
from backend.config import Settings
from backend.auth.models import User  # User model lives here in this repo
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.modules.nets.config_service import set_net_config
from backend.modules.events.models import Event, EventType, EventStatus
from backend.modules.events.routes import events_router
from backend.integrations.weather import service as weather_service
from tests.conftest import make_test_token

BASE = "/api/nets/t/events"


@pytest.fixture(autouse=True)
def _clear_cache():
    weather_service.clear_weather_cache()
    yield


@pytest.fixture
def app_and_ids():
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret")
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(User(callsign="KD0TST", oidc_subject="x|v", name="V"))
        net = Net(slug="t", name="T"); db.add(net); db.flush()
        db.add(NetMembership(user_callsign="KD0TST", net_id=net.id, role=NetRole.VIEWER))
        ev = Event(net_id=net.id, name="E", event_type=EventType.EMERGENCY,
                   status=EventStatus.ACTIVE, created_by="KD0TST",
                   aprs_range_lat=44.98, aprs_range_lon=-93.27)
        db.add(ev); db.commit()
        ev_id = ev.id
    app = FastAPI(); app.state.session_factory = factory; app.state.settings = settings
    app.include_router(events_router)
    return app, settings, ev_id


async def _viewer(app, settings):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       cookies={"access_token": make_test_token("KD0TST", settings)})


@pytest.mark.asyncio
async def test_weather_disabled_by_default(app_and_ids):
    app, settings, ev_id = app_and_ids
    async with await _viewer(app, settings) as c:
        r = await c.get(f"{BASE}/{ev_id}/weather")
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


@pytest.mark.asyncio
async def test_weather_enabled_flag_on_event_detail(app_and_ids, monkeypatch):
    app, settings, ev_id = app_and_ids
    with app.state.session_factory() as db:
        net_id = db.get(Event, ev_id).net_id
        set_net_config(db, net_id, "weather.enabled", "true")
    async with await _viewer(app, settings) as c:
        r = await c.get(f"{BASE}/{ev_id}")
    assert r.status_code == 200
    # GET /{event_id} returns _snapshot: {"event": {...}, "posts": [...], ...}
    assert r.json()["event"]["weather_enabled"] is True


@pytest.mark.asyncio
async def test_weather_route_cross_net_404(app_and_ids):
    app, settings, ev_id = app_and_ids
    async with await _viewer(app, settings) as c:
        r = await c.get(f"{BASE}/99999/weather")
    assert r.status_code == 404
