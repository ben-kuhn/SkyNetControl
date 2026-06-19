import pytest
from datetime import date, time
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.modules.roster.models import RosterTemplate
from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
from backend.modules.checkins.models import CheckIn, ParseStatus, TimingStatus
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
            day_of_week=3,
            time=time(18, 0),
        )
        session.add_all([admin, viewer, season])
        session.flush()

        net_session = NetSession(
            season_id=season.id,
            start_date=date(2026, 4, 10),
            end_date=date(2026, 4, 10),
            grace_period_hours=24.0,
            session_type=SessionType.REGULAR_CHECKIN,
            net_control_callsign="W0NE",
        )
        session.add(net_session)
        session.flush()

        template = RosterTemplate(
            name="Default Roster",
            subject_template="Roster — {{ date }}",
            header_template="NCS: {{ net_control }}, Count: {{ total_count }}",
            welcome_template="{% for m in new_members %}Welcome {{ m.name }}!\n{% endfor %}",
            comments_template="{% for c in checkins %}{% if c.comments %}{{ c.callsign }}: {{ c.comments }}\n{% endif %}{% endfor %}",  # noqa: E501
            footer_template="73 de {{ net_callsign }}",
            lead_time_days=1,
            is_default=True,
        )
        session.add(template)
        session.flush()

        ci = CheckIn(
            session_id=net_session.id,
            callsign="W0TST",
            name="Test Op",
            mode="winlink",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
            is_new_member=True,
            city="Denver",
            state="CO",
            comments="Hello!",
            latitude=39.7,
            longitude=-104.9,
        )
        session.add(ci)
        session.commit()

        yield {
            "engine": engine,
            "factory": factory,
            "admin": admin,
            "viewer": viewer,
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


def _auth_cookie(settings, callsign, role):
    token = create_access_token(callsign, role.value, settings)
    return {"access_token": token}


@pytest.fixture
async def admin_client(app, test_settings, db_setup):
    transport = ASGITransport(app=app)
    cookies = _auth_cookie(test_settings, "W0NE", UserRole.ADMIN)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as c:
        yield c


@pytest.fixture
async def viewer_client(app, test_settings, db_setup):
    transport = ASGITransport(app=app)
    cookies = _auth_cookie(test_settings, "KD0TST", UserRole.VIEWER)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as c:
        yield c


# --- Template CRUD routes ---


@pytest.mark.anyio
async def test_create_template(admin_client):
    resp = await admin_client.post(
        "/api/roster/templates",
        json={
            "name": "Custom",
            "subject_template": "s",
            "header_template": "h",
            "welcome_template": "w",
            "comments_template": "c",
            "footer_template": "f",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Custom"


@pytest.mark.anyio
async def test_list_templates(admin_client):
    resp = await admin_client.get("/api/roster/templates")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.anyio
async def test_update_template(admin_client, db_setup):
    tid = db_setup["template"].id
    resp = await admin_client.patch(f"/api/roster/templates/{tid}", json={"name": "Updated"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated"


@pytest.mark.anyio
async def test_delete_template_blocked_if_default(admin_client, db_setup):
    tid = db_setup["template"].id
    resp = await admin_client.delete(f"/api/roster/templates/{tid}")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_viewer_cannot_create_template(viewer_client):
    resp = await viewer_client.post(
        "/api/roster/templates",
        json={
            "name": "X",
            "subject_template": "s",
            "header_template": "h",
            "welcome_template": "w",
            "comments_template": "c",
            "footer_template": "f",
        },
    )
    assert resp.status_code == 403


# --- Generation routes ---


@pytest.mark.anyio
async def test_generate_draft_for_session(admin_client, db_setup):
    sid = db_setup["net_session"].id
    resp = await admin_client.post(f"/api/roster/generate/{sid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "draft"
    assert data["session_id"] == sid


@pytest.mark.anyio
async def test_generate_draft_session_not_found(admin_client):
    resp = await admin_client.post("/api/roster/generate/999")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_generate_due_drafts(admin_client, db_setup):
    with db_setup["factory"]() as db:
        ns = db.get(NetSession, db_setup["net_session"].id)
        ns.status = SessionStatus.COMPLETED
        db.commit()

    with patch("backend.modules.roster.service._today", return_value=date(2026, 4, 11)):
        resp = await admin_client.post("/api/roster/generate")
    assert resp.status_code == 200
    assert resp.json()["generated"] >= 1


# --- Roster management routes ---


@pytest.mark.anyio
async def test_list_rosters(admin_client, db_setup):
    sid = db_setup["net_session"].id
    await admin_client.post(f"/api/roster/generate/{sid}")
    resp = await admin_client.get("/api/roster/")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.anyio
async def test_list_rosters_filter_status(admin_client, db_setup):
    sid = db_setup["net_session"].id
    await admin_client.post(f"/api/roster/generate/{sid}")
    resp = await admin_client.get("/api/roster/?status=draft")
    assert resp.status_code == 200
    assert all(r["status"] == "draft" for r in resp.json())


@pytest.mark.anyio
async def test_list_rosters_invalid_status(admin_client):
    resp = await admin_client.get("/api/roster/?status=invalid")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_get_roster_for_session(admin_client, db_setup):
    sid = db_setup["net_session"].id
    await admin_client.post(f"/api/roster/generate/{sid}")
    resp = await admin_client.get(f"/api/roster/session/{sid}")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == sid


@pytest.mark.anyio
async def test_preview_roster(admin_client, db_setup):
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"/api/roster/generate/{sid}")
    rid = gen_resp.json()["id"]
    resp = await admin_client.get(f"/api/roster/{rid}/preview")
    assert resp.status_code == 200
    data = resp.json()
    assert "text" in data
    assert "W0TST" in data["text"]


@pytest.mark.anyio
async def test_update_draft_route(admin_client, db_setup):
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"/api/roster/generate/{sid}")
    rid = gen_resp.json()["id"]
    resp = await admin_client.patch(f"/api/roster/{rid}", json={"content_header": "Edited"})
    assert resp.status_code == 200
    assert resp.json()["content_header"] == "Edited"


@pytest.mark.anyio
async def test_approve_roster_route(admin_client, db_setup):
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"/api/roster/generate/{sid}")
    rid = gen_resp.json()["id"]
    resp = await admin_client.post(f"/api/roster/{rid}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert resp.json()["approved_by"] == "W0NE"


@pytest.mark.anyio
async def test_send_roster_route(admin_client, db_setup):
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"/api/roster/generate/{sid}")
    rid = gen_resp.json()["id"]
    await admin_client.post(f"/api/roster/{rid}/approve")
    with patch(
        "backend.integrations.delivery.service.dispatch_delivery",
        return_value=True,
    ):
        resp = await admin_client.post(f"/api/roster/{rid}/send")
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"


@pytest.mark.anyio
async def test_skip_roster_route(admin_client, db_setup):
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"/api/roster/generate/{sid}")
    rid = gen_resp.json()["id"]
    resp = await admin_client.post(f"/api/roster/{rid}/skip")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


@pytest.mark.anyio
async def test_approve_non_draft_returns_409(admin_client, db_setup):
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"/api/roster/generate/{sid}")
    rid = gen_resp.json()["id"]
    await admin_client.post(f"/api/roster/{rid}/approve")
    resp = await admin_client.post(f"/api/roster/{rid}/approve")
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_regenerate_roster_route_rewrites_draft(admin_client, db_setup):
    """Net control / admin can regenerate a draft from the current state."""
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"/api/roster/generate/{sid}")
    rid = gen_resp.json()["id"]

    with db_setup["factory"]() as session:
        from backend.modules.roster.models import RosterLog

        log = session.get(RosterLog, rid)
        log.content_subject = "Stale subject"
        session.commit()

    resp = await admin_client.post(f"/api/roster/{rid}/regenerate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == rid
    assert data["status"] == "draft"
    assert data["content_subject"] != "Stale subject"


@pytest.mark.anyio
async def test_regenerate_roster_route_404_when_missing(admin_client):
    resp = await admin_client.post("/api/roster/9999/regenerate")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_regenerate_roster_route_409_when_not_draft(admin_client, db_setup):
    """Approved rosters can't be regenerated."""
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"/api/roster/generate/{sid}")
    rid = gen_resp.json()["id"]
    await admin_client.post(f"/api/roster/{rid}/approve")

    resp = await admin_client.post(f"/api/roster/{rid}/regenerate")
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_regenerate_roster_route_requires_role(viewer_client, admin_client, db_setup):
    """Viewer cannot regenerate."""
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"/api/roster/generate/{sid}")
    rid = gen_resp.json()["id"]

    resp = await viewer_client.post(f"/api/roster/{rid}/regenerate")
    assert resp.status_code == 403


# --- GeoJSON route ---
