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
async def test_delete_checkin(test_client, test_settings, db_setup):
    with db_setup() as session:
        checkin = CheckIn(
            session_id=1,
            callsign="W0SPAM",
            name="Misparsed Entry",
            mode="Winlink",
            parse_status=ParseStatus.MANUAL_REVIEW,
            timing_status=TimingStatus.ON_TIME,
        )
        session.add(checkin)
        session.commit()
        checkin_id = checkin.id

    token = create_access_token("W0NC", "net_control", test_settings)
    resp = await test_client.delete(
        f"/api/checkins/{checkin_id}",
        cookies={"access_token": token},
    )
    assert resp.status_code == 204

    # Second delete is a 404, not a 500 — operator can press delete
    # twice on a flaky network without breaking the page.
    resp2 = await test_client.delete(
        f"/api/checkins/{checkin_id}",
        cookies={"access_token": token},
    )
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_delete_checkin_viewer_denied(test_client, test_settings, db_setup):
    with db_setup() as session:
        checkin = CheckIn(
            session_id=1,
            callsign="W0DEL",
            name="x",
            mode="Winlink",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
        )
        session.add(checkin)
        session.commit()
        checkin_id = checkin.id

    token = create_access_token("KD0TST", "viewer", test_settings)
    resp = await test_client.delete(
        f"/api/checkins/{checkin_id}",
        cookies={"access_token": token},
    )
    assert resp.status_code == 403


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


@pytest.mark.asyncio
async def test_get_checkins_by_callsign_returns_history(test_client, test_settings, db_setup):
    """Returns the callsign's check-ins across sessions with embedded session_date."""
    from backend.modules.checkins.models import CheckIn, ParseStatus, TimingStatus

    with db_setup() as session:
        # db_setup already provides at least one NetSession; create a check-in on it.
        net_session = session.query(NetSession).first()
        checkin = CheckIn(
            session_id=net_session.id,
            callsign="W0NE",
            name="Test",
            mode="Winlink",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
            is_new_member=False,
        )
        session.add(checkin)
        session.commit()

    viewer_token = create_access_token("KD0TST", "viewer", test_settings)
    resp = await test_client.get(
        "/api/checkins/by-callsign/w0ne",  # case-insensitive
        cookies={"access_token": viewer_token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["callsign"] == "W0NE"
    assert body[0]["mode"] == "Winlink"
    assert "session_date" in body[0]  # embedded for frontend convenience


@pytest.mark.asyncio
async def test_get_checkins_by_callsign_empty(test_client, test_settings):
    """Unknown callsign returns 200 with empty list, not 404."""
    viewer_token = create_access_token("KD0TST", "viewer", test_settings)
    resp = await test_client.get(
        "/api/checkins/by-callsign/NOBODY",
        cookies={"access_token": viewer_token},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_checkins_by_callsign_requires_auth(test_client):
    """Endpoint requires authentication."""
    resp = await test_client.get("/api/checkins/by-callsign/W0NE")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_session_checkins_public_completed(test_client, test_settings, db_setup):
    """Anonymous viewers can fetch check-ins for a COMPLETED session."""
    from backend.modules.checkins.models import CheckIn, ParseStatus, TimingStatus
    from backend.modules.schedule.models import SessionStatus

    with db_setup() as session:
        net_session = session.query(NetSession).first()
        net_session.status = SessionStatus.COMPLETED
        checkin = CheckIn(
            session_id=net_session.id,
            callsign="W0NE",
            name="Test",
            mode="Winlink",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
            is_new_member=False,
        )
        session.add(checkin)
        session.commit()
        net_session_id = net_session.id

    resp = await test_client.get(f"/api/checkins/session/{net_session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["callsign"] == "W0NE"


@pytest.mark.asyncio
async def test_get_session_checkins_public_not_completed_returns_404(test_client, db_setup):
    """Anonymous viewers cannot fetch check-ins for a non-COMPLETED session."""
    with db_setup() as session:
        net_session = session.query(NetSession).first()
        net_session_id = net_session.id

    resp = await test_client.get(f"/api/checkins/session/{net_session_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_session_checkins_public_unknown_session_returns_404(test_client):
    """Anonymous viewers see 404 for unknown sessions (same as not-completed)."""
    resp = await test_client.get("/api/checkins/session/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_session_checkins_authenticated_sees_non_completed(test_client, test_settings, db_setup):
    """Authenticated callers can fetch check-ins for any session status."""
    with db_setup() as session:
        net_session = session.query(NetSession).first()
        net_session_id = net_session.id

    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.get(
        f"/api/checkins/session/{net_session_id}",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_session_checkins_pending_user_sees_only_completed(test_client, test_settings, db_setup):
    """Audit M3: PENDING users are treated like anonymous viewers."""
    # Seed a PENDING user and try to read a non-COMPLETED session.
    with db_setup() as session:
        pending = User(
            callsign="PENDING-x",
            oidc_subject="auth0|pending",
            name="Pending",
            role=UserRole.PENDING,
        )
        session.add(pending)
        session.commit()
        net_session = session.query(NetSession).first()
        net_session_id = net_session.id

    token = create_access_token("PENDING-x", "pending", test_settings)
    resp = await test_client.get(
        f"/api/checkins/session/{net_session_id}",
        cookies={"access_token": token},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_checkins_includes_raw_message(test_client, test_settings, db_setup):
    """The list response exposes the joined raw_message body for parser-derived rows."""
    from backend.modules.checkins.models import RawMessage, MessageType

    with db_setup() as session:
        raw = RawMessage(
            message_id="<raw-1@x>",
            from_address="w0abc@winlink.org",
            received_at=datetime.now(tz=timezone.utc),
            subject="//WL2K Check-in",
            body="John, W0ABC, Denver, CO, Voice",
            message_type=MessageType.PLAIN_TEXT,
            parsed=True,
            source_path="/tmp/x.b2f",
        )
        session.add(raw)
        session.flush()
        session.add(CheckIn(
            session_id=1,
            raw_message_id=raw.id,
            callsign="W0ABC",
            name="John",
            mode="Voice",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
        ))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/checkins/session/1",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    rows = response.json()
    parser_row = next(r for r in rows if r["callsign"] == "W0ABC")
    assert parser_row["raw_message"] is not None
    assert parser_row["raw_message"]["body"] == "John, W0ABC, Denver, CO, Voice"
    assert parser_row["raw_message"]["subject"] == "//WL2K Check-in"
    assert parser_row["raw_message"]["from_address"] == "w0abc@winlink.org"
    assert "received_at" in parser_row["raw_message"]


@pytest.mark.asyncio
async def test_list_checkins_raw_message_null_for_manual(test_client, test_settings, db_setup):
    """Manual check-ins serialize raw_message as null."""
    with db_setup() as session:
        session.add(CheckIn(
            session_id=1,
            raw_message_id=None,
            callsign="W0XYZ",
            name="Hand-entered",
            mode="Voice",
            parse_status=ParseStatus.MANUALLY_ENTERED,
            timing_status=TimingStatus.ON_TIME,
        ))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/checkins/session/1",
        cookies={"access_token": token},
    )
    rows = response.json()
    manual_row = next(r for r in rows if r["callsign"] == "W0XYZ")
    assert manual_row["raw_message"] is None


@pytest.mark.asyncio
async def test_winlink_form_checkin_response_includes_form_view_html(
    test_client, test_settings, db_setup, tmp_path, monkeypatch
):
    """A winlink_form check-in surfaces form_view_html + raw_message.message_type."""
    from backend.config import settings
    from backend.modules.checkins.models import RawMessage, MessageType
    from backend.modules.forms import library

    # Make forms_library_dir resolve to a tmp dir + seed a fake template
    # so the renderer returns the template path, not the KV fallback.
    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    (forms_dir / "Test_Check_in.html").write_text("<html><body>{callsign}</body></html>")

    body = """<?xml version="1.0"?>
<RMS_Express_Form>
  <form_parameters><display_form>Test_Check_in.html</display_form></form_parameters>
  <variables>
    <var name="Callsign">KU0HN</var>
    <var name="Name">Ben</var>
    <var name="Mode">Voice</var>
  </variables>
</RMS_Express_Form>"""

    with db_setup() as session:
        raw = RawMessage(
            message_id="<wlf@x>",
            from_address="ku0hn@winlink.org",
            received_at=datetime.now(tz=timezone.utc),
            subject="check-in",
            body=body,
            message_type=MessageType.WINLINK_FORM,
            parsed=True,
        )
        session.add(raw)
        session.flush()
        session.add(CheckIn(
            session_id=1,
            raw_message_id=raw.id,
            callsign="KU0HN",
            name="Ben",
            mode="Voice",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
        ))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.get(
        "/api/checkins/session/1",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    row = next(r for r in resp.json() if r["callsign"] == "KU0HN")
    assert row["raw_message"]["message_type"] == "winlink_form"
    assert row["form_view_html"] is not None
    assert "KU0HN" in row["form_view_html"]


@pytest.mark.asyncio
async def test_non_winlink_checkin_response_has_null_form_view_html(
    test_client, test_settings, db_setup
):
    from backend.modules.checkins.models import RawMessage, MessageType

    with db_setup() as session:
        raw = RawMessage(
            message_id="<pt@x>",
            from_address="w0abc@winlink.org",
            received_at=datetime.now(tz=timezone.utc),
            subject="check-in",
            body="W0ABC, John, Denver, CO, Voice",
            message_type=MessageType.PLAIN_TEXT,
            parsed=True,
        )
        session.add(raw)
        session.flush()
        session.add(CheckIn(
            session_id=1,
            raw_message_id=raw.id,
            callsign="W0ABC",
            name="John",
            mode="Voice",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
        ))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.get(
        "/api/checkins/session/1",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    row = next(r for r in resp.json() if r["callsign"] == "W0ABC")
    assert row["raw_message"]["message_type"] == "plain_text"
    assert row["form_view_html"] is None
