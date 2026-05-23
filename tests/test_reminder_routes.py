import pytest
from datetime import date, datetime, time, timezone
from unittest.mock import patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.modules.reminders.routes import reminders_router
from backend.modules.reminders.models import ReminderLog, ReminderStatus, ReminderTemplate, TemplateType
from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
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
        net_control = User(
            callsign="W0NC",
            oidc_subject="auth0|netcontrol",
            name="Net Control",
            role=UserRole.NET_CONTROL,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
            role=UserRole.VIEWER,
        )
        season = NetSeason(
            name="Spring 2026",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 6, 30),
            day_of_week=3,  # Thursday
            time=time(18, 0),
        )
        session.add_all([admin, net_control, viewer, season])
        session.flush()

        net_session = NetSession(
            season_id=season.id,
            start_date=date(2026, 4, 10),
            end_date=date(2026, 4, 10),
            grace_period_hours=24.0,
            session_type=SessionType.REGULAR_CHECKIN,
            status=SessionStatus.SCHEDULED,
            net_control_callsign="W0NE",
        )
        session.add(net_session)
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(reminders_router, prefix="/api/reminders")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- Template tests ---


@pytest.mark.asyncio
async def test_create_template(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/reminders/templates",
        json={
            "name": "Regular Reminder",
            "template_type": "regular_checkin",
            "subject_template": "Net on {{ date }}",
            "body_template": "Check in on {{ date }} at {{ time }}.",
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Regular Reminder"
    assert data["template_type"] == "regular_checkin"
    assert data["id"] is not None
    assert data["lead_time_days"] == 2
    assert data["is_default"] is False


@pytest.mark.asyncio
async def test_list_templates(test_client, test_settings, db_setup):
    with db_setup() as session:
        tmpl = ReminderTemplate(
            name="Seeded Template",
            template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="Subject",
            body_template="Body",
            lead_time_days=2,
            is_default=False,
        )
        session.add(tmpl)
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/reminders/templates",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Seeded Template"


@pytest.mark.asyncio
async def test_update_template(test_client, test_settings, db_setup):
    with db_setup() as session:
        tmpl = ReminderTemplate(
            name="Original Name",
            template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="Subject",
            body_template="Body",
            lead_time_days=2,
            is_default=False,
        )
        session.add(tmpl)
        session.commit()
        tmpl_id = tmpl.id

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.patch(
        f"/api/reminders/templates/{tmpl_id}",
        json={"name": "Updated Name"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"


@pytest.mark.asyncio
async def test_delete_template(test_client, test_settings, db_setup):
    with db_setup() as session:
        tmpl = ReminderTemplate(
            name="To Delete",
            template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="Subject",
            body_template="Body",
            lead_time_days=2,
            is_default=False,
        )
        session.add(tmpl)
        session.commit()
        tmpl_id = tmpl.id

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.delete(
        f"/api/reminders/templates/{tmpl_id}",
        cookies={"access_token": token},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_cannot_delete_default_template(test_client, test_settings, db_setup):
    with db_setup() as session:
        tmpl = ReminderTemplate(
            name="Default Template",
            template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="Subject",
            body_template="Body",
            lead_time_days=2,
            is_default=True,
        )
        session.add(tmpl)
        session.commit()
        tmpl_id = tmpl.id

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.delete(
        f"/api/reminders/templates/{tmpl_id}",
        cookies={"access_token": token},
    )
    assert response.status_code == 400


# --- Generation tests ---


@pytest.mark.asyncio
async def test_generate_draft_for_session(test_client, test_settings, db_setup):
    with db_setup() as session:
        tmpl = ReminderTemplate(
            name="Default Regular",
            template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="Net on {{ date }}",
            body_template="Check in on {{ date }} at {{ time }}.",
            lead_time_days=2,
            is_default=True,
        )
        session.add(tmpl)
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/reminders/generate/1",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "draft"
    assert "April 10, 2026" in data["content_subject"]
    assert data["session_id"] == 1


@pytest.mark.asyncio
async def test_generate_due_drafts(test_client, test_settings, db_setup):
    with db_setup() as session:
        tmpl = ReminderTemplate(
            name="Default Regular",
            template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="Net on {{ date }}",
            body_template="Check in on {{ date }}.",
            lead_time_days=3,
            is_default=True,
        )
        session.add(tmpl)
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    # session date is April 10, 2026 — mock today to April 8 so it's within lead time of 3
    with patch("backend.modules.reminders.service._today", return_value=date(2026, 4, 8)):
        response = await test_client.post(
            "/api/reminders/generate",
            cookies={"access_token": token},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["generated"] == 1
    assert len(data["reminders"]) == 1


# --- Reminder read tests ---


@pytest.mark.asyncio
async def test_get_reminder_for_session(test_client, test_settings, db_setup):
    with db_setup() as session:
        log = ReminderLog(
            session_id=1,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Test Subject",
            content_body="Test Body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        session.add(log)
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/reminders/session/1",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["content_subject"] == "Test Subject"
    assert data["session_id"] == 1


# --- Status transition tests ---


@pytest.mark.asyncio
async def test_approve_reminder(test_client, test_settings, db_setup):
    with db_setup() as session:
        log = ReminderLog(
            session_id=1,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Subject",
            content_body="Body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        session.add(log)
        session.commit()
        log_id = log.id

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        f"/api/reminders/{log_id}/approve",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approved"
    assert data["approved_by"] == "W0NE"
    assert data["approved_at"] is not None


@pytest.mark.asyncio
async def test_send_reminder(test_client, test_settings, db_setup):
    with db_setup() as session:
        log = ReminderLog(
            session_id=1,
            template_id=None,
            status=ReminderStatus.APPROVED,
            content_subject="Subject",
            content_body="Body",
            drafted_at=datetime.now(tz=timezone.utc),
            approved_at=datetime.now(tz=timezone.utc),
            approved_by="W0NE",
        )
        session.add(log)
        session.commit()
        log_id = log.id

    token = create_access_token("W0NE", "admin", test_settings)
    with patch(
        "backend.integrations.delivery.service.dispatch_delivery",
        return_value=True,
    ):
        response = await test_client.post(
            f"/api/reminders/{log_id}/send",
            cookies={"access_token": token},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "sent"
    assert data["sent_at"] is not None


@pytest.mark.asyncio
async def test_skip_reminder(test_client, test_settings, db_setup):
    with db_setup() as session:
        log = ReminderLog(
            session_id=1,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Subject",
            content_body="Body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        session.add(log)
        session.commit()
        log_id = log.id

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        f"/api/reminders/{log_id}/skip",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "skipped"


@pytest.mark.asyncio
async def test_edit_draft(test_client, test_settings, db_setup):
    with db_setup() as session:
        log = ReminderLog(
            session_id=1,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Old Subject",
            content_body="Old Body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        session.add(log)
        session.commit()
        log_id = log.id

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.patch(
        f"/api/reminders/{log_id}",
        json={"content_subject": "New Subject", "content_body": "New Body"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["content_subject"] == "New Subject"
    assert data["content_body"] == "New Body"


# --- Filter and permission tests ---


@pytest.mark.asyncio
async def test_list_reminders_with_status_filter(test_client, test_settings, db_setup):
    with db_setup() as session:
        log = ReminderLog(
            session_id=1,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Subject",
            content_body="Body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        session.add(log)
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)

    response = await test_client.get(
        "/api/reminders/?status=draft",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1

    response = await test_client.get(
        "/api/reminders/?status=sent",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert len(response.json()) == 0


@pytest.mark.asyncio
async def test_viewer_can_read_but_not_generate(test_client, test_settings, db_setup):
    with db_setup() as session:
        tmpl = ReminderTemplate(
            name="Default Regular",
            template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="Net on {{ date }}",
            body_template="Check in on {{ date }}.",
            lead_time_days=2,
            is_default=True,
        )
        session.add(tmpl)
        session.commit()

    viewer_token = create_access_token("KD0TST", "viewer", test_settings)

    # Viewer can list reminders
    response = await test_client.get(
        "/api/reminders/",
        cookies={"access_token": viewer_token},
    )
    assert response.status_code == 200

    # Viewer cannot generate drafts
    response = await test_client.post(
        "/api/reminders/generate/1",
        cookies={"access_token": viewer_token},
    )
    assert response.status_code == 403


# --- Regeneration tests ---


@pytest.mark.asyncio
async def test_regenerate_reminder_route_rewrites_draft(test_client, test_settings, db_setup):
    """Net control can regenerate a draft from the current session and template."""
    with db_setup() as session:
        tmpl = ReminderTemplate(
            name="Default Regular",
            template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="Net on {{ date }}",
            body_template="Check in on {{ date }}.",
            lead_time_days=2,
            is_default=True,
        )
        session.add(tmpl)
        session.flush()

        log = ReminderLog(
            session_id=1,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Stale subject",
            content_body="Stale body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        session.add(log)
        session.commit()
        log_id = log.id

    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.post(
        f"/api/reminders/{log_id}/regenerate",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == log_id
    assert data["status"] == "draft"
    assert data["content_subject"] != "Stale subject"
    assert data["content_body"] != "Stale body"


@pytest.mark.asyncio
async def test_regenerate_reminder_route_404_when_missing(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.post(
        "/api/reminders/999/regenerate",
        cookies={"access_token": token},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_regenerate_reminder_route_409_when_not_draft(test_client, test_settings, db_setup):
    """Approved reminders can't be regenerated."""
    with db_setup() as session:
        log = ReminderLog(
            session_id=1,
            template_id=None,
            status=ReminderStatus.APPROVED,
            content_subject="Subject",
            content_body="Body",
            drafted_at=datetime.now(tz=timezone.utc),
            approved_at=datetime.now(tz=timezone.utc),
            approved_by="W0NE",
        )
        session.add(log)
        session.commit()
        log_id = log.id

    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.post(
        f"/api/reminders/{log_id}/regenerate",
        cookies={"access_token": token},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_regenerate_reminder_route_requires_role(test_client, test_settings, db_setup):
    """Viewer cannot regenerate."""
    with db_setup() as session:
        log = ReminderLog(
            session_id=1,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="S",
            content_body="B",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        session.add(log)
        session.commit()
        log_id = log.id

    viewer_token = create_access_token("KD0TST", "viewer", test_settings)
    resp = await test_client.post(
        f"/api/reminders/{log_id}/regenerate",
        cookies={"access_token": viewer_token},
    )
    assert resp.status_code == 403
