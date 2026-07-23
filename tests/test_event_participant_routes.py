"""Participant route tests — rewritten for net-independent /api/events routes."""
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
            User(callsign="W0NC", oidc_subject="auth0|nc", name="NC"),
            User(callsign="KD0TST", oidc_subject="auth0|v", name="V"),
        ])
        session.commit()
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = settings
    app.include_router(events_router)
    return app, settings


def _c(app, settings, callsign=None):
    ck = {"access_token": make_test_token(callsign, settings)} if callsign else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=ck)


@pytest.fixture
async def active_event(app_s):
    """Creates an ACTIVE event owned by W0NC; returns (app, settings, event_id)."""
    app, settings = app_s
    async with _c(app, settings, "W0NC") as c:
        r = await c.post(BASE, json={"name": "SKYWARN", "event_type": "emergency"})
        assert r.status_code == 201
        eid = r.json()["id"]
        r2 = await c.post(f"{BASE}/{eid}/activate")
        assert r2.status_code == 200
    return app, settings, eid


class TestParticipantRoutes:
    async def test_check_in(self, active_event):
        app, settings, eid = active_event
        async with _c(app, settings, "W0NC") as c:
            resp = await c.post(f"{BASE}/{eid}/participants", json={"callsign": "ke0xyz"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["callsign"] == "KE0XYZ"
        assert body["current_status"] == "checked_in"

    async def test_duplicate_check_in_409(self, active_event):
        app, settings, eid = active_event
        async with _c(app, settings, "W0NC") as c:
            await c.post(f"{BASE}/{eid}/participants", json={"callsign": "KE0XYZ"})
            resp = await c.post(f"{BASE}/{eid}/participants", json={"callsign": "KE0XYZ"})
        assert resp.status_code == 409

    async def test_status_change_and_invalid_transition(self, active_event):
        app, settings, eid = active_event
        async with _c(app, settings, "W0NC") as c:
            pid = (await c.post(
                f"{BASE}/{eid}/participants", json={"callsign": "KE0XYZ"}
            )).json()["id"]
            resp = await c.patch(
                f"{BASE}/{eid}/participants/{pid}", json={"status": "checked_out"}
            )
            assert resp.status_code == 200
            assert resp.json()["current_status"] == "checked_out"
            resp = await c.patch(
                f"{BASE}/{eid}/participants/{pid}", json={"status": "at_post"}
            )
        assert resp.status_code == 422

    async def test_unassign_post_via_null(self, active_event):
        app, settings, eid = active_event
        async with _c(app, settings, "W0NC") as c:
            post_id = (await c.post(
                f"{BASE}/{eid}/posts", json={"name": "EOC"}
            )).json()["id"]
            pid = (await c.post(
                f"{BASE}/{eid}/participants",
                json={"callsign": "KE0XYZ", "post_id": post_id},
            )).json()["id"]
            resp = await c.patch(
                f"{BASE}/{eid}/participants/{pid}", json={"post_id": None}
            )
        assert resp.status_code == 200
        assert resp.json()["post_id"] is None

    async def test_write_on_closed_event_409(self, active_event):
        app, settings, eid = active_event
        async with _c(app, settings, "W0NC") as c:
            await c.post(f"{BASE}/{eid}/close")
            resp = await c.post(f"{BASE}/{eid}/participants", json={"callsign": "KE0XYZ"})
        assert resp.status_code == 409

    async def test_viewer_cannot_write(self, active_event):
        """KD0TST is not an operator of W0NC's event — cannot check in participants."""
        app, settings, eid = active_event
        async with _c(app, settings, "KD0TST") as c:
            resp = await c.post(f"{BASE}/{eid}/participants", json={"callsign": "KE0XYZ"})
        assert resp.status_code == 403


class TestLogRoutes:
    async def test_add_note(self, active_event):
        app, settings, eid = active_event
        async with _c(app, settings, "W0NC") as c:
            resp = await c.post(f"{BASE}/{eid}/log", json={"message": "Course clear"})
        assert resp.status_code == 201
        assert resp.json()["entry_type"] == "note"

    async def test_participant_note_with_pin(self, active_event):
        app, settings, eid = active_event
        async with _c(app, settings, "W0NC") as c:
            await c.post(f"{BASE}/{eid}/participants", json={"callsign": "KE0XYZ"})
            resp = await c.post(
                f"{BASE}/{eid}/log",
                json={"message": "Medic", "callsign": "ke0xyz", "pinned": True},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["entry_type"] == "participant_note"
        assert body["callsign"] == "KE0XYZ"
        assert body["pinned"] is True

    async def test_pin_toggle(self, active_event):
        app, settings, eid = active_event
        async with _c(app, settings, "W0NC") as c:
            entry_id = (await c.post(
                f"{BASE}/{eid}/log", json={"message": "x"}
            )).json()["id"]
            resp = await c.patch(f"{BASE}/{eid}/log/{entry_id}", json={"pinned": True})
        assert resp.status_code == 200
        assert resp.json()["pinned"] is True


class TestUpdatesRoute:
    async def test_cursor_semantics(self, active_event):
        app, settings, eid = active_event
        # since=0 → full log (activation entry)
        async with _c(app, settings, "W0NC") as c:
            resp = await c.get(f"{BASE}/{eid}/updates", params={"since": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert body["latest_seq"] == 1
        assert len(body["log"]) == 1

        async with _c(app, settings, "W0NC") as c:
            await c.post(f"{BASE}/{eid}/participants", json={"callsign": "KE0XYZ"})

        # catch-up from previous cursor → only the new entry
        async with _c(app, settings, "W0NC") as c:
            resp = await c.get(f"{BASE}/{eid}/updates", params={"since": 1})
        body = resp.json()
        assert body["latest_seq"] == 2
        assert len(body["log"]) == 1
        assert body["log"][0]["seq"] == 2
        # state always included whole
        assert len(body["participants"]) == 1

        # up-to-date cursor → empty delta
        async with _c(app, settings, "W0NC") as c:
            resp = await c.get(f"{BASE}/{eid}/updates", params={"since": 2})
        assert resp.json()["log"] == []

    async def test_pinned_seqs_propagate_through_cursor(self, active_event):
        app, settings, eid = active_event
        async with _c(app, settings, "W0NC") as c:
            note_resp = await c.post(f"{BASE}/{eid}/log", json={"message": "important"})
            note_id = note_resp.json()["id"]
            note_seq = note_resp.json()["seq"]
            latest_seq = note_seq

            # Pin the note
            await c.patch(f"{BASE}/{eid}/log/{note_id}", json={"pinned": True})

            # Poll with since=latest_seq: log delta is empty, but pinned_seqs includes the seq
            resp = await c.get(f"{BASE}/{eid}/updates", params={"since": latest_seq})
        body = resp.json()
        assert body["log"] == []
        assert note_seq in body["pinned_seqs"]

        async with _c(app, settings, "W0NC") as c:
            # Unpin and confirm it disappears from pinned_seqs
            await c.patch(f"{BASE}/{eid}/log/{note_id}", json={"pinned": False})
            resp = await c.get(f"{BASE}/{eid}/updates", params={"since": latest_seq})
        assert note_seq not in resp.json()["pinned_seqs"]


class TestReportRoute:
    async def test_report(self, active_event):
        app, settings, eid = active_event
        async with _c(app, settings, "W0NC") as c:
            pid = (await c.post(
                f"{BASE}/{eid}/participants", json={"callsign": "KE0XYZ"}
            )).json()["id"]
            await c.patch(
                f"{BASE}/{eid}/participants/{pid}", json={"status": "checked_out"}
            )
            resp = await c.get(f"{BASE}/{eid}/report")
        assert resp.status_code == 200
        participants = resp.json()["participants"]
        assert len(participants) == 1
        assert participants[0]["callsign"] == "KE0XYZ"
        assert len(participants[0]["stints"]) == 1
        assert participants[0]["stints"][0]["end"] is not None
