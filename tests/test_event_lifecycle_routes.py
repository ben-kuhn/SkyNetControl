"""Lifecycle route tests — rewritten for net-independent /api/events routes."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.events.routes import events_router
from tests.conftest import make_test_token

BASE = "/api/events"


@pytest.fixture
def app_s():
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([
            User(callsign="W0NC", oidc_subject="auth0|nc", name="Net Control"),
            User(callsign="KD0TST", oidc_subject="auth0|viewer", name="Viewer"),
            User(callsign="W0OUT", oidc_subject="auth0|out", name="Outsider"),
        ])
        session.commit()
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = settings
    app.include_router(events_router)
    return app, settings


def _client(app, settings, callsign=None):
    ck = {"access_token": make_test_token(callsign, settings)} if callsign else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=ck)


DRAFT_BODY = {"name": "Marathon", "event_type": "public_service"}
# Note: no "activate" field — activation is a separate POST /{id}/activate
ACTIVE_BODY = {"name": "Tornado", "event_type": "emergency"}


class TestLifecycleRoutes:
    async def test_create_draft(self, app_s):
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            resp = await c.post(BASE, json=DRAFT_BODY)
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "draft"
        assert body["event_type"] == "public_service"
        assert body["owner"] == "W0NC"

    async def test_create_and_activate(self, app_s):
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            r = await c.post(BASE, json=ACTIVE_BODY)
            assert r.status_code == 201
            eid = r.json()["id"]
            r2 = await c.post(f"{BASE}/{eid}/activate")
            assert r2.status_code == 200
            assert r2.json()["status"] == "active"

    async def test_list_events(self, app_s):
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            await c.post(BASE, json=DRAFT_BODY)
            await c.post(BASE, json=ACTIVE_BODY)
            resp = await c.get(BASE)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_get_snapshot(self, app_s):
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json=ACTIVE_BODY)).json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
            resp = await c.get(f"{BASE}/{event_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["event"]["id"] == event_id
        assert body["posts"] == []
        assert body["participants"] == []
        assert len(body["log"]) == 1  # "Event activated"

    async def test_activate_close_reopen(self, app_s):
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json=DRAFT_BODY)).json()["id"]
            assert (await c.post(f"{BASE}/{event_id}/activate")).status_code == 200
            assert (await c.post(f"{BASE}/{event_id}/activate")).status_code == 409
            assert (await c.post(f"{BASE}/{event_id}/close")).status_code == 200
            assert (await c.post(f"{BASE}/{event_id}/close")).status_code == 409
            resp = await c.post(f"{BASE}/{event_id}/reopen")
            assert resp.status_code == 200
            assert resp.json()["status"] == "active"

    async def test_patch_event(self, app_s):
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json=DRAFT_BODY)).json()["id"]
            resp = await c.patch(f"{BASE}/{event_id}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    async def test_patch_closed_event_409(self, app_s):
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json=ACTIVE_BODY)).json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
            await c.post(f"{BASE}/{event_id}/close")
            resp = await c.patch(f"{BASE}/{event_id}", json={"name": "Nope"})
        assert resp.status_code == 409


class TestPostRoutes:
    async def test_post_crud(self, app_s):
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json=ACTIVE_BODY)).json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
            resp = await c.post(f"{BASE}/{event_id}/posts", json={"name": "EOC", "lat": 39.1, "lon": -94.6})
            assert resp.status_code == 201
            post_id = resp.json()["id"]

            dup = await c.post(f"{BASE}/{event_id}/posts", json={"name": "EOC"})
            assert dup.status_code == 409

            resp = await c.patch(f"{BASE}/{event_id}/posts/{post_id}", json={"name": "EOC Main"})
            assert resp.status_code == 200
            assert resp.json()["name"] == "EOC Main"

            resp = await c.delete(f"{BASE}/{event_id}/posts/{post_id}")
            assert resp.status_code == 204

    async def test_update_post_on_closed_event_409(self, app_s):
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json=ACTIVE_BODY)).json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
            post_id = (await c.post(f"{BASE}/{event_id}/posts", json={"name": "EOC"})).json()["id"]
            await c.post(f"{BASE}/{event_id}/close")
            resp = await c.patch(f"{BASE}/{event_id}/posts/{post_id}", json={"name": "Nope"})
        assert resp.status_code == 409

    async def test_delete_post_on_closed_event_409(self, app_s):
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json=ACTIVE_BODY)).json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
            post_id = (await c.post(f"{BASE}/{event_id}/posts", json={"name": "EOC"})).json()["id"]
            await c.post(f"{BASE}/{event_id}/close")
            resp = await c.delete(f"{BASE}/{event_id}/posts/{post_id}")
        assert resp.status_code == 409

    async def test_pin_on_closed_event_409(self, app_s):
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json=ACTIVE_BODY)).json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
            note = (await c.post(f"{BASE}/{event_id}/log", json={"message": "x"})).json()
            await c.post(f"{BASE}/{event_id}/close")
            resp = await c.patch(f"{BASE}/{event_id}/log/{note['id']}", json={"pinned": True})
        assert resp.status_code == 409

    async def test_empty_event_name_422(self, app_s):
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            resp = await c.post(BASE, json={"name": "", "event_type": "emergency"})
        assert resp.status_code == 422

    async def test_empty_post_name_422(self, app_s):
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json=ACTIVE_BODY)).json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
            resp = await c.post(f"{BASE}/{event_id}/posts", json={"name": ""})
        assert resp.status_code == 422

    async def test_null_participant_status_422(self, app_s):
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json=ACTIVE_BODY)).json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
            pid = (await c.post(
                f"{BASE}/{event_id}/participants", json={"callsign": "KE0XYZ"}
            )).json()["id"]
            resp = await c.patch(f"{BASE}/{event_id}/participants/{pid}", json={"status": None})
        assert resp.status_code == 422


class TestPermissions:
    async def test_owner_can_read(self, app_s):
        """Owner (W0NC) can read their own event."""
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json=ACTIVE_BODY)).json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
            assert (await c.get(BASE)).status_code == 200
            assert (await c.get(f"{BASE}/{event_id}")).status_code == 200

    async def test_outsider_cannot_see_private_event(self, app_s):
        """W0OUT (not owner/operator) cannot access a private event."""
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json=ACTIVE_BODY)).json()["id"]
        async with _client(app, settings, "W0OUT") as c:
            # Private event: outsider gets 403 or 404
            r = await c.get(f"{BASE}/{event_id}")
            assert r.status_code in (403, 404)

    async def test_owner_cannot_write_on_closed_event(self, app_s):
        """Writing to a closed event returns 409."""
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json=ACTIVE_BODY)).json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
            await c.post(f"{BASE}/{event_id}/close")
            resp = await c.post(f"{BASE}/{event_id}/posts", json={"name": "X"})
        assert resp.status_code == 409

    async def test_viewer_cannot_write(self, app_s):
        """W0OUT is not an operator of W0NC's event — cannot create posts."""
        app, settings = app_s
        async with _client(app, settings, "W0NC") as c:
            event_id = (await c.post(BASE, json=ACTIVE_BODY)).json()["id"]
            await c.post(f"{BASE}/{event_id}/activate")
        async with _client(app, settings, "W0OUT") as c:
            resp = await c.post(f"{BASE}/{event_id}/posts", json={"name": "X"})
        assert resp.status_code in (403, 404)

    async def test_anonymous_denied(self, app_s):
        app, settings = app_s
        async with _client(app, settings) as c:
            assert (await c.get(BASE)).status_code == 401
