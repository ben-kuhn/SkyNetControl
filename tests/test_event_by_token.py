import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import Settings
from backend.db.base import Base
from backend.auth.models import User
from backend.modules.events.models import Event, EventType, EventStatus
from backend.auth.dependencies import get_db_session
from backend.modules.events.routes import events_router


@pytest.fixture
def app_ctx():
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret")
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all(
            [
                User(callsign="OWNER", oidc_subject="x|o", name="O"),
            ]
        )
        pub = Event(
            name="Public Event",
            event_type=EventType.EMERGENCY,
            status=EventStatus.ACTIVE,
            created_by="OWNER",
            public_token="pub-tok-123",
            visibility="public",
        )
        priv = Event(
            name="Private Event",
            event_type=EventType.EMERGENCY,
            status=EventStatus.ACTIVE,
            created_by="OWNER",
            public_token="priv-tok-456",
            visibility="private",
        )
        db.add_all([pub, priv])
        db.commit()
        ids = {
            "public_event": pub.id,
            "public_token": pub.public_token,
            "private_event": priv.id,
            "private_token": priv.public_token,
        }
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = settings
    app.include_router(events_router)

    # Override get_db_session to use the test DB
    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db_session] = override_db

    return app, settings, ids


def _anon_client(app, settings):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_by_token_returns_public_snapshot(app_ctx):
    app, settings, ids = app_ctx
    tok = ids["public_token"]
    async with _anon_client(app, settings) as c:
        r = await c.get(f"/api/events/by-token/{tok}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == ids["public_event"]
        assert body["is_control"] is False
        assert "public_token" not in body
        assert "operators" not in body
        assert "messages" not in body


@pytest.mark.asyncio
async def test_by_token_404s_private_and_bad_token(app_ctx):
    app, settings, ids = app_ctx
    async with _anon_client(app, settings) as c:
        assert (await c.get("/api/events/by-token/does-not-exist")).status_code == 404
        # a PRIVATE event's token must not resolve
        assert (await c.get(f"/api/events/by-token/{ids['private_token']}")).status_code == 404
