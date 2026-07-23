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
