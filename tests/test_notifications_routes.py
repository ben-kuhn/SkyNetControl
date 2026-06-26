"""Route tests for /api/nets/{net_slug}/notifications/..."""
import pytest
from datetime import date, datetime, time, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.modules.notifications.models import Notification, NotificationKind
from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
from backend.config import Settings
from tests.conftest import make_test_token


NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/notifications"


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
        admin = User(callsign="W0NE", oidc_subject="auth0|admin", name="Admin", is_admin=True)
        viewer = User(callsign="KD0TST", oidc_subject="auth0|viewer", name="Viewer")
        other = User(callsign="K0OTH", oidc_subject="auth0|other", name="Other")
        net = Net(slug=NET_SLUG, name="Test Net")
        session.add_all([admin, viewer, other, net])
        session.flush()

        session.add(NetMembership(user_callsign="KD0TST", net_id=net.id, role=NetRole.VIEWER))
        session.add(NetMembership(user_callsign="K0OTH", net_id=net.id, role=NetRole.VIEWER))

        season = NetSeason(
            net_id=net.id,
            name="Spring 2026",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 6, 30),
            day_of_week=3,
            time=time(18, 0),
        )
        session.add(season)
        session.flush()

        net_session = NetSession(
            season_id=season.id,
            start_date=date(2026, 4, 10),
            session_type=SessionType.REGULAR_CHECKIN,
            status=SessionStatus.SCHEDULED,
            net_control_callsign="W0NE",
        )
        session.add(net_session)
        session.commit()

        yield {
            "engine": engine,
            "factory": factory,
            "admin": admin,
            "viewer": viewer,
            "net": net,
            "season": season,
            "net_session": net_session,
        }
    engine.dispose()


@pytest.fixture
def app(test_settings, db_setup):
    from backend.app import create_app

    application = create_app(settings=test_settings)
    application.state.engine = db_setup["engine"]
    application.state.session_factory = db_setup["factory"]
    return application


@pytest.fixture
async def admin_client(app, test_settings):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token}) as c:
        yield c


@pytest.fixture
async def viewer_client(app, test_settings):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    token = make_test_token("KD0TST", test_settings, token_version=0)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token}) as c:
        yield c


@pytest.fixture
async def anon_client(app):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _seed_notification(db_setup, callsign: str, read: bool = False, kind=NotificationKind.REMINDER_DRAFT) -> int:
    """Insert a notification linked to the test net's session."""
    with db_setup["factory"]() as session:
        n = Notification(
            recipient_callsign=callsign,
            kind=kind,
            message="Test notification",
            link_url="/reminders",
            session_id=db_setup["net_session"].id,
            created_at=datetime.now(tz=timezone.utc),
            read_at=datetime.now(tz=timezone.utc) if read else None,
        )
        session.add(n)
        session.commit()
        return n.id


# --- Basic read/list tests ---


@pytest.mark.anyio
async def test_list_returns_unread_only_by_default(admin_client, db_setup):
    _seed_notification(db_setup, "W0NE")
    _seed_notification(db_setup, "W0NE", read=True)

    resp = await admin_client.get(f"{BASE}/")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["read_at"] is None


@pytest.mark.anyio
async def test_list_with_all_includes_read(admin_client, db_setup):
    _seed_notification(db_setup, "W0NE")
    _seed_notification(db_setup, "W0NE", read=True)

    resp = await admin_client.get(f"{BASE}/?all=1")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.anyio
async def test_list_only_returns_users_own(admin_client, db_setup):
    _seed_notification(db_setup, "W0NE")
    _seed_notification(db_setup, "K0OTH")

    resp = await admin_client.get(f"{BASE}/")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["message"] == "Test notification"


@pytest.mark.anyio
async def test_mark_one_read(admin_client, db_setup):
    nid = _seed_notification(db_setup, "W0NE")

    resp = await admin_client.post(f"{BASE}/{nid}/read")
    assert resp.status_code == 200
    assert resp.json()["read_at"] is not None


@pytest.mark.anyio
async def test_mark_one_read_not_owned_returns_404(admin_client, db_setup):
    nid = _seed_notification(db_setup, "KD0TST")

    resp = await admin_client.post(f"{BASE}/{nid}/read")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_mark_all_read(admin_client, db_setup):
    _seed_notification(db_setup, "W0NE")
    _seed_notification(db_setup, "W0NE")
    _seed_notification(db_setup, "K0OTH")

    resp = await admin_client.post(f"{BASE}/read-all")
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


# --- Auth / permission tests ---


@pytest.mark.anyio
async def test_list_requires_auth(anon_client):
    resp = await anon_client.get(f"{BASE}/")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_read_all_requires_auth(anon_client):
    resp = await anon_client.post(f"{BASE}/read-all")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_non_member_gets_403(app, test_settings, db_setup):
    """A user who exists but has no membership in this net gets 403."""
    from httpx import ASGITransport, AsyncClient

    # Insert a user with no membership
    with db_setup["factory"]() as session:
        nomem = User(callsign="W0NOMEM", oidc_subject="auth0|nomem", name="No Mem")
        session.add(nomem)
        session.commit()

    transport = ASGITransport(app=app)
    token = make_test_token("W0NOMEM", test_settings, token_version=0)
    async with AsyncClient(
        transport=transport, base_url="http://test", cookies={"access_token": token}
    ) as c:
        resp = await c.get(f"{BASE}/")
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_viewer_can_list(viewer_client):
    resp = await viewer_client.get(f"{BASE}/")
    assert resp.status_code == 200


# --- Cross-net isolation tests ---


@pytest.mark.anyio
async def test_cross_net_notification_not_visible_in_other_net(admin_client, db_setup):
    """A notification tied to net A does NOT appear under net B's URL."""
    with db_setup["factory"]() as session:
        net_b = Net(slug="net-b", name="Net B")
        session.add(net_b)
        session.flush()
        season_b = NetSeason(
            net_id=net_b.id,
            name="S",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            day_of_week=3,
            time=time(18, 0),
        )
        session.add(season_b)
        session.flush()
        session_b = NetSession(
            season_id=season_b.id,
            start_date=date(2026, 5, 1),
            session_type=SessionType.REGULAR_CHECKIN,
            status=SessionStatus.SCHEDULED,
            net_control_callsign="W0NE",
        )
        session.add(session_b)
        session.flush()
        # Notification for W0NE tied to net B's session
        n = Notification(
            recipient_callsign="W0NE",
            kind=NotificationKind.REMINDER_DRAFT,
            message="Net B notification",
            session_id=session_b.id,
            created_at=datetime.now(tz=timezone.utc),
        )
        session.add(n)
        session.commit()
        net_b_notif_id = n.id

    # Also seed a notification in net A for W0NE
    net_a_notif_id = _seed_notification(db_setup, "W0NE")

    # Under net A's URL — only net A notification should be visible
    resp = await admin_client.get(f"{BASE}/")
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()}
    assert net_a_notif_id in ids
    assert net_b_notif_id not in ids


@pytest.mark.anyio
async def test_mark_read_wrong_net_returns_404(admin_client, db_setup):
    """POST /nets/t/notifications/{id}/read where id belongs to net B returns 404."""
    with db_setup["factory"]() as session:
        net_b = Net(slug="net-b2", name="Net B2")
        session.add(net_b)
        session.flush()
        season_b = NetSeason(
            net_id=net_b.id,
            name="S",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            day_of_week=3,
            time=time(18, 0),
        )
        session.add(season_b)
        session.flush()
        session_b = NetSession(
            season_id=season_b.id,
            start_date=date(2026, 5, 1),
            session_type=SessionType.REGULAR_CHECKIN,
            status=SessionStatus.SCHEDULED,
            net_control_callsign="W0NE",
        )
        session.add(session_b)
        session.flush()
        n = Notification(
            recipient_callsign="W0NE",
            kind=NotificationKind.REMINDER_DRAFT,
            message="Net B notification",
            session_id=session_b.id,
            created_at=datetime.now(tz=timezone.utc),
        )
        session.add(n)
        session.commit()
        net_b_notif_id = n.id

    # Try to mark net B's notification as read via net A's URL
    resp = await admin_client.post(f"{BASE}/{net_b_notif_id}/read")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_mark_all_read_only_affects_current_net(admin_client, db_setup):
    """mark_all_read on net A does NOT mark net B notifications read."""
    with db_setup["factory"]() as session:
        net_b = Net(slug="net-b3", name="Net B3")
        session.add(net_b)
        session.flush()
        season_b = NetSeason(
            net_id=net_b.id,
            name="S",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            day_of_week=3,
            time=time(18, 0),
        )
        session.add(season_b)
        session.flush()
        session_b = NetSession(
            season_id=season_b.id,
            start_date=date(2026, 5, 1),
            session_type=SessionType.REGULAR_CHECKIN,
            status=SessionStatus.SCHEDULED,
            net_control_callsign="W0NE",
        )
        session.add(session_b)
        session.flush()
        n_b = Notification(
            recipient_callsign="W0NE",
            kind=NotificationKind.REMINDER_DRAFT,
            message="Net B notification",
            session_id=session_b.id,
            created_at=datetime.now(tz=timezone.utc),
        )
        session.add(n_b)
        session.commit()
        net_b_notif_id = n_b.id

    # Seed one notification in net A
    _seed_notification(db_setup, "W0NE")

    # mark_all_read on net A — should return count=1 (only net A notification)
    resp = await admin_client.post(f"{BASE}/read-all")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1

    # Net B's notification should still be unread
    with db_setup["factory"]() as session:
        n = session.get(Notification, net_b_notif_id)
        assert n.read_at is None
