"""
TDD regression: message list and attachment download routes must require CONTROL.

The public event page (map/log/weather) must NEVER expose Winlink message content
to anonymous or non-control viewers.  Before the fix, GET /{id}/messages was gated
at READ, so a public-token holder could fetch message content.

Step 1 (RED): test_messages_list_requires_control fails — anon token gets 200.
Step 2 (GREEN): after changing both routes to EventRole.CONTROL, all assertions pass.
test_read_snapshot_excludes_message_content is an audit test that verifies the READ
path (GET /{id} and GET /{id}/updates) never includes message content; it should
already pass and must continue to do so.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.events.models import Event, EventOperator, EventType, EventStatus
from tests.conftest import make_test_token

BASE = "/api/events"


@pytest.fixture
def app_ctx():
    """Mirror the fixture in test_event_auth.py: public event with a public_token."""
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret")
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all(
            [
                User(callsign="OWNER", oidc_subject="x|o", name="O"),
                User(callsign="OTHER", oidc_subject="x|ot", name="T"),
            ]
        )
        pub = Event(
            name="PubEvent",
            event_type=EventType.EMERGENCY,
            status=EventStatus.ACTIVE,
            created_by="OWNER",
            public_token="utok",
            visibility="public",
        )
        db.add(pub)
        db.flush()
        db.commit()
        ids = {"public_event": pub.id, "public_token": "utok"}

    from backend.app import create_app
    application = create_app(settings=settings)
    application.state.engine = engine
    application.state.session_factory = factory

    return application, settings, ids


def _anon_client(app, settings):
    """No auth header — anonymous visitor."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _other_client(app, settings):
    """Authenticated user who is not an event operator (non-control)."""
    token = make_test_token("OTHER", settings)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       cookies={"access_token": token})


def _control_client(app, settings):
    """Event owner — has CONTROL on the event."""
    token = make_test_token("OWNER", settings)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       cookies={"access_token": token})


@pytest.mark.asyncio
async def test_messages_list_requires_control(app_ctx):
    """GET /{id}/messages must now require CONTROL, not READ.

    anonymous + valid public token  → 401 or 404 (never 200)
    authenticated non-control       → 403
    control (owner)                 → 200
    """
    app, settings, ids = app_ctx
    pub = ids["public_event"]
    tok = ids["public_token"]

    # anonymous with valid token on a PUBLIC event: was 200 (READ) — must now be 401/404 (CONTROL)
    async with _anon_client(app, settings) as c:
        resp = await c.get(f"{BASE}/{pub}/messages?token={tok}")
        assert resp.status_code in (401, 404), (
            f"Expected 401 or 404 for anon+public-token on CONTROL route, got {resp.status_code}"
        )

    # authenticated non-control on public event: 403
    async with _other_client(app, settings) as c:
        resp = await c.get(f"{BASE}/{pub}/messages")
        assert resp.status_code == 403, (
            f"Expected 403 for authed non-control on CONTROL route, got {resp.status_code}"
        )

    # control user (owner): still 200
    async with _control_client(app, settings) as c:
        resp = await c.get(f"{BASE}/{pub}/messages")
        assert resp.status_code == 200, (
            f"Expected 200 for control user on messages route, got {resp.status_code}"
        )


@pytest.mark.asyncio
async def test_read_snapshot_excludes_message_content(app_ctx):
    """Audit: the READ snapshot (GET /{id} and GET /{id}/updates) must never
    include a 'messages' key — message content is CONTROL-only."""
    app, settings, ids = app_ctx
    pub = ids["public_event"]
    tok = ids["public_token"]

    async with _anon_client(app, settings) as c:
        detail_resp = await c.get(f"{BASE}/{pub}?token={tok}")
        assert detail_resp.status_code == 200, f"Expected 200 on detail, got {detail_resp.status_code}"
        detail = detail_resp.json()

        upd_resp = await c.get(f"{BASE}/{pub}/updates?token={tok}")
        assert upd_resp.status_code == 200, f"Expected 200 on updates, got {upd_resp.status_code}"
        upd = upd_resp.json()

    for name, body in (("detail", detail), ("updates", upd)):
        assert "messages" not in body, (
            f"READ path '{name}' must not include 'messages' key — message content is CONTROL-only"
        )
