import pytest
from datetime import datetime, timezone
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.auth.service import create_access_token
from backend.modules.notifications.routes import notifications_router
from backend.modules.notifications.models import Notification, NotificationKind
from backend.config import Settings
from tests.conftest import make_test_token



@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )


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
        admin = User(callsign="W0NE", oidc_subject="x|a", name="Admin", is_admin=True)
        viewer = User(callsign="KD0TST", oidc_subject="x|v", name="Viewer", )
        session.add_all([admin, viewer])
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(notifications_router, prefix="/api/notifications")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _seed(db_setup, callsign, kind=NotificationKind.REMINDER_DRAFT, read=False):
    with db_setup() as session:
        n = Notification(
            recipient_callsign=callsign,
            kind=kind,
            message="Test",
            link_url="/reminders",
            created_at=datetime.now(tz=timezone.utc),
            read_at=datetime.now(tz=timezone.utc) if read else None,
        )
        session.add(n)
        session.commit()
        return n.id


@pytest.mark.asyncio
async def test_list_returns_unread_only_by_default(test_client, test_settings, db_setup):
    _seed(db_setup, "W0NE")
    _seed(db_setup, "W0NE", read=True)

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    resp = await test_client.get(
        "/api/notifications/",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["read_at"] is None


@pytest.mark.asyncio
async def test_list_with_all_includes_read(test_client, test_settings, db_setup):
    _seed(db_setup, "W0NE")
    _seed(db_setup, "W0NE", read=True)

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    resp = await test_client.get(
        "/api/notifications/?all=1",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_list_only_returns_users_own(test_client, test_settings, db_setup):
    _seed(db_setup, "W0NE")
    _seed(db_setup, "KD0TST")

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    resp = await test_client.get(
        "/api/notifications/",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1


@pytest.mark.asyncio
async def test_mark_one_read(test_client, test_settings, db_setup):
    nid = _seed(db_setup, "W0NE")

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    resp = await test_client.post(
        f"/api/notifications/{nid}/read",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert resp.json()["read_at"] is not None


@pytest.mark.asyncio
async def test_mark_one_read_not_owned_returns_404(test_client, test_settings, db_setup):
    nid = _seed(db_setup, "KD0TST")

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    resp = await test_client.post(
        f"/api/notifications/{nid}/read",
        cookies={"access_token": token},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mark_all_read(test_client, test_settings, db_setup):
    _seed(db_setup, "W0NE")
    _seed(db_setup, "W0NE")
    _seed(db_setup, "KD0TST")

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    resp = await test_client.post(
        "/api/notifications/read-all",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert resp.json() == {"count": 2}


@pytest.mark.asyncio
async def test_list_requires_auth(test_client):
    resp = await test_client.get("/api/notifications/")
    assert resp.status_code == 401
