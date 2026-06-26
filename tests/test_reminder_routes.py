import pytest
from datetime import date, datetime, time, timezone
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.modules.reminders.models import ReminderLog, ReminderStatus, ReminderTemplate, TemplateType
from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.config import Settings
from tests.conftest import make_test_token


NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/reminders"


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
            is_admin=True,
        )
        net_control = User(
            callsign="W0NC",
            oidc_subject="auth0|netcontrol",
            name="Net Control",
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
        )
        net = Net(slug=NET_SLUG, name="Test Net")
        session.add_all([admin, net_control, viewer, net])
        session.flush()

        # Viewer gets a net membership so require_net_role(VIEWER) passes
        session.add(NetMembership(user_callsign="KD0TST", net_id=net.id, role=NetRole.VIEWER))
        # Net control gets NET_CONTROL membership
        session.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))

        season = NetSeason(
            net_id=net.id,
            name="Spring 2026",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 6, 30),
            day_of_week=3,  # Thursday
            time=time(18, 0),
        )
        session.add(season)
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
        session.flush()

        template = ReminderTemplate(
            net_id=net.id,
            name="Default Regular",
            template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="Net on {{ date }}",
            body_template="Check in on {{ date }} at {{ time }}.",
            lead_time_days=2,
            is_default=True,
        )
        session.add(template)
        session.commit()

        yield {
            "engine": engine,
            "factory": factory,
            "admin": admin,
            "viewer": viewer,
            "net": net,
            "season": season,
            "net_session": net_session,
            "template": template,
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
async def admin_client(app, test_settings, db_setup):
    transport = ASGITransport(app=app)
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    cookies = {"access_token": token}
    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as c:
        yield c


@pytest.fixture
async def viewer_client(app, test_settings, db_setup):
    transport = ASGITransport(app=app)
    token = make_test_token("KD0TST", test_settings, token_version=0)
    cookies = {"access_token": token}
    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as c:
        yield c


# --- Template routes ---


@pytest.mark.anyio
async def test_create_template(admin_client):
    resp = await admin_client.post(
        f"{BASE}/templates",
        json={
            "name": "Custom Reminder",
            "template_type": "regular_checkin",
            "subject_template": "Net on {{ date }}",
            "body_template": "Check in on {{ date }}.",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Custom Reminder"
    assert data["template_type"] == "regular_checkin"
    assert "net_id" in data
    assert data["id"] is not None


@pytest.mark.anyio
async def test_list_templates(admin_client):
    resp = await admin_client.get(f"{BASE}/templates")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.anyio
async def test_viewer_can_list_templates(viewer_client):
    resp = await viewer_client.get(f"{BASE}/templates")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_viewer_cannot_create_template(viewer_client):
    resp = await viewer_client.post(
        f"{BASE}/templates",
        json={
            "name": "Viewer Template",
            "template_type": "regular_checkin",
            "subject_template": "S",
            "body_template": "B",
        },
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_update_template(admin_client, db_setup):
    tid = db_setup["template"].id
    resp = await admin_client.patch(f"{BASE}/templates/{tid}", json={"name": "Updated Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"


@pytest.mark.anyio
async def test_delete_template_blocked_if_default(admin_client, db_setup):
    tid = db_setup["template"].id
    resp = await admin_client.delete(f"{BASE}/templates/{tid}")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_delete_template(admin_client, db_setup):
    with db_setup["factory"]() as db:
        tmpl = ReminderTemplate(
            net_id=db_setup["net"].id,
            name="Deletable",
            template_type=TemplateType.ACTIVITY,
            subject_template="S",
            body_template="B",
            lead_time_days=2,
            is_default=False,
        )
        db.add(tmpl)
        db.commit()
        tmpl_id = tmpl.id

    resp = await admin_client.delete(f"{BASE}/templates/{tmpl_id}")
    assert resp.status_code == 204


# --- Template defaults ---


@pytest.mark.anyio
async def test_template_defaults_returns_seed_list(admin_client):
    """The endpoint returns the shipped seeds in genericized form."""
    resp = await admin_client.get(f"{BASE}/template-defaults")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    types = {d["template_type"] for d in data}
    assert types == {"regular_checkin", "activity"}
    for d in data:
        assert "W0NE" not in d["subject_template"]
        assert "{{ net_callsign }}" in d["subject_template"]


@pytest.mark.anyio
async def test_template_defaults_requires_role(viewer_client):
    """Viewer cannot see the defaults endpoint."""
    resp = await viewer_client.get(f"{BASE}/template-defaults")
    assert resp.status_code == 403


# --- Cross-net rejection ---


@pytest.mark.anyio
async def test_template_cross_net_404(admin_client, db_setup):
    """A template belonging to a different net returns 404 on PATCH/DELETE."""
    with db_setup["factory"]() as db:
        net2 = Net(slug="other-net", name="Other Net")
        db.add(net2)
        db.flush()
        tmpl2 = ReminderTemplate(
            net_id=net2.id,
            name="Other Net Template",
            template_type=TemplateType.REGULAR_CHECKIN,
            subject_template="S",
            body_template="B",
            lead_time_days=2,
            is_default=False,
        )
        db.add(tmpl2)
        db.commit()
        tmpl2_id = tmpl2.id

    # PATCH against net "t" but template belongs to "other-net"
    resp = await admin_client.patch(f"{BASE}/templates/{tmpl2_id}", json={"name": "Hijack"})
    assert resp.status_code == 404

    # DELETE against net "t" but template belongs to "other-net"
    resp = await admin_client.delete(f"{BASE}/templates/{tmpl2_id}")
    assert resp.status_code == 404


# --- Generation routes ---


@pytest.mark.anyio
async def test_generate_draft_for_session(admin_client, db_setup):
    session_id = db_setup["net_session"].id
    response = await admin_client.post(f"{BASE}/generate/{session_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "draft"
    assert "April 10, 2026" in data["content_subject"]
    assert data["session_id"] == session_id


@pytest.mark.anyio
async def test_generate_due_drafts(admin_client, db_setup):
    # session date is April 10, 2026 — mock today to April 8 so it's within lead time of 2
    with patch("backend.modules.reminders.service._today", return_value=date(2026, 4, 8)):
        response = await admin_client.post(f"{BASE}/generate")
    assert response.status_code == 200
    data = response.json()
    assert data["generated"] == 1
    assert len(data["reminders"]) == 1


@pytest.mark.anyio
async def test_viewer_cannot_generate(viewer_client, db_setup):
    session_id = db_setup["net_session"].id
    resp = await viewer_client.post(f"{BASE}/generate/{session_id}")
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_generate_cross_net_session_404(admin_client, db_setup):
    """Generating for a session in another net should return 404."""
    with db_setup["factory"]() as db:
        net2 = Net(slug="net2", name="Net 2")
        db.add(net2)
        db.flush()
        season2 = NetSeason(
            net_id=net2.id,
            name="Season 2",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 6, 30),
            day_of_week=3,
            time=time(18, 0),
        )
        db.add(season2)
        db.flush()
        session2 = NetSession(
            season_id=season2.id,
            start_date=date(2026, 4, 10),
            end_date=date(2026, 4, 10),
            grace_period_hours=24.0,
            session_type=SessionType.REGULAR_CHECKIN,
            status=SessionStatus.SCHEDULED,
        )
        db.add(session2)
        db.commit()
        session2_id = session2.id

    resp = await admin_client.post(f"{BASE}/generate/{session2_id}")
    assert resp.status_code == 404


# --- Reminder list and detail ---


@pytest.mark.anyio
async def test_list_reminders(admin_client, db_setup):
    with db_setup["factory"]() as db:
        log = ReminderLog(
            session_id=db_setup["net_session"].id,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Test Subject",
            content_body="Test Body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        db.add(log)
        db.commit()

    resp = await admin_client.get(f"{BASE}/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert any(r["content_subject"] == "Test Subject" for r in data)


@pytest.mark.anyio
async def test_viewer_can_list_reminders(viewer_client, db_setup):
    resp = await viewer_client.get(f"{BASE}/")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_list_reminders_cross_net_isolation(admin_client, db_setup):
    """Reminders from another net's sessions are not returned."""
    with db_setup["factory"]() as db:
        net2 = Net(slug="net2-iso", name="Net 2 Isolation")
        db.add(net2)
        db.flush()
        season2 = NetSeason(
            net_id=net2.id,
            name="Season 2",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 6, 30),
            day_of_week=3,
            time=time(18, 0),
        )
        db.add(season2)
        db.flush()
        session2 = NetSession(
            season_id=season2.id,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 1),
            grace_period_hours=24.0,
            session_type=SessionType.REGULAR_CHECKIN,
            status=SessionStatus.SCHEDULED,
        )
        db.add(session2)
        db.flush()
        log2 = ReminderLog(
            session_id=session2.id,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Net2 Subject",
            content_body="Net2 Body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        db.add(log2)
        db.commit()

    resp = await admin_client.get(f"{BASE}/")
    assert resp.status_code == 200
    subjects = [r["content_subject"] for r in resp.json()]
    assert "Net2 Subject" not in subjects


@pytest.mark.anyio
async def test_get_reminder_for_session(admin_client, db_setup):
    with db_setup["factory"]() as db:
        log = ReminderLog(
            session_id=db_setup["net_session"].id,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Session Subject",
            content_body="Session Body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        db.add(log)
        db.commit()

    session_id = db_setup["net_session"].id
    resp = await admin_client.get(f"{BASE}/session/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["content_subject"] == "Session Subject"


@pytest.mark.anyio
async def test_list_reminders_with_status_filter(admin_client, db_setup):
    with db_setup["factory"]() as db:
        log = ReminderLog(
            session_id=db_setup["net_session"].id,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Subject",
            content_body="Body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        db.add(log)
        db.commit()

    resp = await admin_client.get(f"{BASE}/?status=draft")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    resp = await admin_client.get(f"{BASE}/?status=sent")
    assert resp.status_code == 200
    assert all(r["status"] == "sent" for r in resp.json())


# --- Reminder action routes ---


@pytest.mark.anyio
async def test_approve_reminder(admin_client, db_setup):
    with db_setup["factory"]() as db:
        log = ReminderLog(
            session_id=db_setup["net_session"].id,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Subject",
            content_body="Body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        db.add(log)
        db.commit()
        log_id = log.id

    resp = await admin_client.post(f"{BASE}/{log_id}/approve")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert data["approved_by"] == "W0NE"
    assert data["approved_at"] is not None


@pytest.mark.anyio
async def test_send_reminder(admin_client, db_setup):
    with db_setup["factory"]() as db:
        log = ReminderLog(
            session_id=db_setup["net_session"].id,
            template_id=None,
            status=ReminderStatus.APPROVED,
            content_subject="Subject",
            content_body="Body",
            drafted_at=datetime.now(tz=timezone.utc),
            approved_at=datetime.now(tz=timezone.utc),
            approved_by="W0NE",
        )
        db.add(log)
        db.commit()
        log_id = log.id

    with patch("backend.integrations.delivery.service.dispatch_delivery", return_value=True):
        resp = await admin_client.post(f"{BASE}/{log_id}/send")
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"


@pytest.mark.anyio
async def test_skip_reminder(admin_client, db_setup):
    with db_setup["factory"]() as db:
        log = ReminderLog(
            session_id=db_setup["net_session"].id,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Subject",
            content_body="Body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        db.add(log)
        db.commit()
        log_id = log.id

    resp = await admin_client.post(f"{BASE}/{log_id}/skip")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


@pytest.mark.anyio
async def test_edit_draft(admin_client, db_setup):
    with db_setup["factory"]() as db:
        log = ReminderLog(
            session_id=db_setup["net_session"].id,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Old Subject",
            content_body="Old Body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        db.add(log)
        db.commit()
        log_id = log.id

    resp = await admin_client.patch(
        f"{BASE}/{log_id}",
        json={"content_subject": "New Subject", "content_body": "New Body"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["content_subject"] == "New Subject"
    assert data["content_body"] == "New Body"


@pytest.mark.anyio
async def test_viewer_cannot_approve(viewer_client, db_setup):
    with db_setup["factory"]() as db:
        log = ReminderLog(
            session_id=db_setup["net_session"].id,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="S",
            content_body="B",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        db.add(log)
        db.commit()
        log_id = log.id

    resp = await viewer_client.post(f"{BASE}/{log_id}/approve")
    assert resp.status_code == 403


# --- Cross-net action rejection ---


@pytest.mark.anyio
async def test_action_cross_net_404(admin_client, db_setup):
    """Actions on reminders from another net return 404, not a 200 or 403."""
    with db_setup["factory"]() as db:
        net2 = Net(slug="action-net2", name="Action Net 2")
        db.add(net2)
        db.flush()
        season2 = NetSeason(
            net_id=net2.id,
            name="S2",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 6, 30),
            day_of_week=3,
            time=time(18, 0),
        )
        db.add(season2)
        db.flush()
        session2 = NetSession(
            season_id=season2.id,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 1),
            grace_period_hours=24.0,
            session_type=SessionType.REGULAR_CHECKIN,
            status=SessionStatus.SCHEDULED,
        )
        db.add(session2)
        db.flush()
        log2 = ReminderLog(
            session_id=session2.id,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Net2 Subject",
            content_body="Net2 Body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        db.add(log2)
        db.commit()
        log2_id = log2.id

    # Admin is global — they can resolve net "t", but log2 belongs to "action-net2"
    resp = await admin_client.post(f"{BASE}/{log2_id}/approve")
    assert resp.status_code == 404

    resp = await admin_client.post(f"{BASE}/{log2_id}/skip")
    assert resp.status_code == 404

    resp = await admin_client.patch(f"{BASE}/{log2_id}", json={"content_subject": "Hijack"})
    assert resp.status_code == 404


# --- Regenerate ---


@pytest.mark.anyio
async def test_regenerate_reminder(admin_client, db_setup):
    """Net control can regenerate a draft."""
    with db_setup["factory"]() as db:
        log = ReminderLog(
            session_id=db_setup["net_session"].id,
            template_id=db_setup["template"].id,
            status=ReminderStatus.DRAFT,
            content_subject="Stale subject",
            content_body="Stale body",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        db.add(log)
        db.commit()
        log_id = log.id

    resp = await admin_client.post(f"{BASE}/{log_id}/regenerate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == log_id
    assert data["status"] == "draft"
    assert data["content_subject"] != "Stale subject"


@pytest.mark.anyio
async def test_regenerate_not_found(admin_client):
    resp = await admin_client.post(f"{BASE}/999/regenerate")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_regenerate_not_draft_409(admin_client, db_setup):
    with db_setup["factory"]() as db:
        log = ReminderLog(
            session_id=db_setup["net_session"].id,
            template_id=None,
            status=ReminderStatus.APPROVED,
            content_subject="Subject",
            content_body="Body",
            drafted_at=datetime.now(tz=timezone.utc),
            approved_at=datetime.now(tz=timezone.utc),
            approved_by="W0NE",
        )
        db.add(log)
        db.commit()
        log_id = log.id

    resp = await admin_client.post(f"{BASE}/{log_id}/regenerate")
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_viewer_cannot_regenerate(viewer_client, db_setup):
    with db_setup["factory"]() as db:
        log = ReminderLog(
            session_id=db_setup["net_session"].id,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="S",
            content_body="B",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        db.add(log)
        db.commit()
        log_id = log.id

    resp = await viewer_client.post(f"{BASE}/{log_id}/regenerate")
    assert resp.status_code == 403


# --- Anonymous access ---


@pytest.mark.anyio
async def test_anonymous_cannot_list_reminders(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(f"{BASE}/")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_anonymous_cannot_list_templates(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(f"{BASE}/templates")
    assert resp.status_code == 401


# --- Per-net seeding ---


@pytest.mark.anyio
async def test_create_net_seeds_reminder_templates(admin_client):
    """Creating a new net via the API seeds reminder templates for it."""
    resp = await admin_client.post(
        "/api/nets",
        json={"slug": "new-net", "name": "New Net"},
    )
    assert resp.status_code == 201

    # The new net's templates should be seeded
    resp2 = await admin_client.get("/api/nets/new-net/reminders/templates")
    assert resp2.status_code == 200
    templates = resp2.json()
    assert len(templates) == 2
    types = {t["template_type"] for t in templates}
    assert types == {"regular_checkin", "activity"}
