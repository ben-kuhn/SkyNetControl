import pytest
from datetime import date, datetime, timezone
from unittest.mock import patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.config_mgmt.models import AppConfig
from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionType,
)
from backend.modules.checkins.models import CheckIn, ParseStatus, TimingStatus
from backend.modules.checkins.routes import checkins_router
from backend.config import Settings


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
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            role=UserRole.ADMIN,
        )
        nc = User(
            callsign="W0NC",
            oidc_subject="auth0|nc",
            name="Net Control",
            role=UserRole.NET_CONTROL,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
            role=UserRole.VIEWER,
        )
        session.add_all([admin, nc, viewer])

        session.add(AppConfig(key="pat_mailbox_path", value="/tmp/test-mailbox"))
        session.add(AppConfig(key="net_address", value="w0ne@winlink.org"))

        season = NetSeason(
            name="Test Season",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 6, 30),
            day_of_week=3,
        )
        session.add(season)
        session.flush()

        net_session = NetSession(
            season_id=season.id,
            start_date=date(2026, 4, 10),
            end_date=date(2026, 4, 10),
            grace_period_hours=24.0,
            session_type=SessionType.REGULAR_CHECKIN,
        )
        session.add(net_session)
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(checkins_router, prefix="/api/checkins")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_scan_mailbox(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)

    mock_messages = [
        {
            "message_id": "SCAN001",
            "from_address": "W0ABC@winlink.org",
            "to_address": "w0ne@winlink.org",
            "subject": "Check-in",
            "received_at": datetime(2026, 4, 10, 18, 30, tzinfo=timezone.utc),
            "body": "Name: John Smith\nCallsign: W0ABC\nCity: Denver\nState: CO\nMode: Winlink\n",
        },
    ]

    with patch("backend.modules.checkins.routes.read_mailbox") as mock_read:
        mock_read.return_value = mock_messages
        response = await test_client.post(
            "/api/checkins/scan/1",
            cookies={"access_token": token},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 1
    assert len(data["checkins"]) == 1
    assert data["checkins"][0]["callsign"] == "W0ABC"


@pytest.mark.asyncio
async def test_get_checkins_for_session(test_client, test_settings, db_setup):
    with db_setup() as session:
        checkin = CheckIn(
            session_id=1,
            callsign="W0ABC",
            name="John Smith",
            mode="Winlink",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
        )
        session.add(checkin)
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/checkins/session/1",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["callsign"] == "W0ABC"


@pytest.mark.asyncio
async def test_create_manual_checkin(test_client, test_settings):
    token = create_access_token("W0NC", "net_control", test_settings)
    response = await test_client.post(
        "/api/checkins/manual",
        json={
            "session_id": 1,
            "callsign": "W0MAN",
            "name": "Manual Entry",
            "mode": "Voice Relay",
            "city": "Pueblo",
            "state": "CO",
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["callsign"] == "W0MAN"
    assert data["parse_status"] == "manually_entered"


@pytest.mark.asyncio
async def test_update_checkin(test_client, test_settings, db_setup):
    with db_setup() as session:
        checkin = CheckIn(
            session_id=1,
            callsign="W0ABC",
            name="John",
            mode="Winlink",
            parse_status=ParseStatus.MANUAL_REVIEW,
            timing_status=TimingStatus.ON_TIME,
        )
        session.add(checkin)
        session.commit()
        checkin_id = checkin.id

    token = create_access_token("W0NC", "net_control", test_settings)
    response = await test_client.patch(
        f"/api/checkins/{checkin_id}",
        json={"name": "John Smith", "city": "Denver"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "John Smith"
    assert response.json()["city"] == "Denver"


@pytest.mark.asyncio
async def test_approve_session(test_client, test_settings, db_setup):
    with db_setup() as session:
        checkin = CheckIn(
            session_id=1,
            callsign="W0NEW",
            name="New Person",
            mode="Winlink",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
            is_new_member=True,
        )
        session.add(checkin)
        session.commit()

    token = create_access_token("W0NC", "net_control", test_settings)
    response = await test_client.post(
        "/api/checkins/approve/1",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["session_status"] == "completed"
    assert data["members_updated"] >= 1


@pytest.mark.asyncio
async def test_viewer_can_read_but_not_scan(test_client, test_settings):
    viewer_token = create_access_token("KD0TST", "viewer", test_settings)

    response = await test_client.get(
        "/api/checkins/session/1",
        cookies={"access_token": viewer_token},
    )
    assert response.status_code == 200

    with patch("backend.modules.checkins.routes.read_mailbox") as mock_read:
        mock_read.return_value = []
        response = await test_client.post(
            "/api/checkins/scan/1",
            cookies={"access_token": viewer_token},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_members(test_client, test_settings, db_setup):
    from backend.modules.checkins.models import Member

    with db_setup() as session:
        member = Member(
            callsign="W0ABC",
            name="John Smith",
            first_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_check_in_date=datetime(2026, 4, 10, tzinfo=timezone.utc),
            total_check_ins=12,
        )
        session.add(member)
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/checkins/members",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["callsign"] == "W0ABC"
    assert data[0]["total_check_ins"] == 12


@pytest.mark.asyncio
async def test_get_modes_returns_default(test_client, test_settings):
    """Any authenticated user can fetch the modes list."""
    viewer_token = create_access_token("KD0TST", "viewer", test_settings)
    resp = await test_client.get(
        "/api/checkins/modes",
        cookies={"access_token": viewer_token},
    )
    assert resp.status_code == 200
    modes = resp.json()
    assert isinstance(modes, list)
    assert "Voice" in modes
    assert "Winlink" in modes
    assert len(modes) == 12
