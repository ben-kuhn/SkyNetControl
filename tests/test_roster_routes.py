import pytest
from datetime import date, time
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.modules.roster.models import RosterTemplate
from tests.conftest import make_test_token
from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
from backend.modules.checkins.models import CheckIn, ParseStatus, TimingStatus
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.config import Settings


NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/roster"


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
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
        )
        net = Net(slug=NET_SLUG, name="Test Net")
        session.add_all([admin, viewer, net])
        session.flush()

        # Viewer gets a net membership so require_net_role(VIEWER) passes
        session.add(NetMembership(user_callsign="KD0TST", net_id=net.id, role=NetRole.VIEWER))

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
            end_date=date(2026, 4, 10),
            grace_period_hours=24.0,
            session_type=SessionType.REGULAR_CHECKIN,
            net_control_callsign="W0NE",
        )
        session.add(net_session)
        session.flush()

        template = RosterTemplate(
            net_id=net.id,
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


# --- Template CRUD routes ---


@pytest.mark.anyio
async def test_create_template(admin_client):
    resp = await admin_client.post(
        f"{BASE}/templates",
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
    assert "net_id" in data


@pytest.mark.anyio
async def test_list_templates(admin_client):
    resp = await admin_client.get(f"{BASE}/templates")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.anyio
async def test_update_template(admin_client, db_setup):
    tid = db_setup["template"].id
    resp = await admin_client.patch(f"{BASE}/templates/{tid}", json={"name": "Updated"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated"


@pytest.mark.anyio
async def test_delete_template_blocked_if_default(admin_client, db_setup):
    tid = db_setup["template"].id
    resp = await admin_client.delete(f"{BASE}/templates/{tid}")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_viewer_cannot_create_template(viewer_client):
    resp = await viewer_client.post(
        f"{BASE}/templates",
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


@pytest.mark.anyio
async def test_template_defaults_returns_seed(admin_client):
    """Endpoint returns the shipped roster seed in genericized form."""
    resp = await admin_client.get(f"{BASE}/template-defaults")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    seed = data[0]
    assert seed["name"] == "Default Net Roster"
    assert "W0NE" not in seed["subject_template"]
    assert "W0NE" not in seed["header_template"]
    assert "W0NE" not in seed["footer_template"]
    assert "{{ net_callsign }}" in seed["footer_template"]


@pytest.mark.anyio
async def test_template_defaults_requires_role(viewer_client):
    """Viewer cannot see the defaults endpoint (matches create's role gate)."""
    resp = await viewer_client.get(f"{BASE}/template-defaults")
    assert resp.status_code == 403


# --- Cross-net rejection ---


@pytest.mark.anyio
async def test_template_cross_net_404(admin_client, db_setup):
    """A template belonging to a different net returns 404."""
    # Create another net and template in it
    with db_setup["factory"]() as db:
        from backend.modules.nets.models import Net
        net2 = Net(slug="other-net", name="Other Net")
        db.add(net2)
        db.flush()
        tmpl2 = RosterTemplate(
            net_id=net2.id,
            name="Other Template",
            subject_template="s",
            header_template="h",
            welcome_template="w",
            comments_template="c",
            footer_template="f",
            is_default=False,
        )
        db.add(tmpl2)
        db.commit()
        other_id = tmpl2.id

    # Accessing it via this net's slug should 404
    resp = await admin_client.patch(f"{BASE}/templates/{other_id}", json={"name": "Hack"})
    assert resp.status_code == 404


# --- Generation routes ---


@pytest.mark.anyio
async def test_generate_draft_for_session(admin_client, db_setup):
    sid = db_setup["net_session"].id
    resp = await admin_client.post(f"{BASE}/generate/{sid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "draft"
    assert data["session_id"] == sid


@pytest.mark.anyio
async def test_generate_draft_session_not_found(admin_client):
    resp = await admin_client.post(f"{BASE}/generate/999")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_generate_due_drafts(admin_client, db_setup):
    with db_setup["factory"]() as db:
        ns = db.get(NetSession, db_setup["net_session"].id)
        ns.status = SessionStatus.COMPLETED
        db.commit()

    with patch("backend.modules.roster.service._today", return_value=date(2026, 4, 11)):
        resp = await admin_client.post(f"{BASE}/generate")
    assert resp.status_code == 200
    assert resp.json()["generated"] >= 1


# --- Roster management routes ---


@pytest.mark.anyio
async def test_list_rosters(admin_client, db_setup):
    sid = db_setup["net_session"].id
    await admin_client.post(f"{BASE}/generate/{sid}")
    resp = await admin_client.get(f"{BASE}/")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.anyio
async def test_list_rosters_filter_status(admin_client, db_setup):
    sid = db_setup["net_session"].id
    await admin_client.post(f"{BASE}/generate/{sid}")
    resp = await admin_client.get(f"{BASE}/?status=draft")
    assert resp.status_code == 200
    assert all(r["status"] == "draft" for r in resp.json())


@pytest.mark.anyio
async def test_list_rosters_invalid_status(admin_client):
    resp = await admin_client.get(f"{BASE}/?status=invalid")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_get_roster_for_session(admin_client, db_setup):
    sid = db_setup["net_session"].id
    await admin_client.post(f"{BASE}/generate/{sid}")
    resp = await admin_client.get(f"{BASE}/session/{sid}")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == sid


@pytest.mark.anyio
async def test_preview_roster(admin_client, db_setup):
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"{BASE}/generate/{sid}")
    rid = gen_resp.json()["id"]
    resp = await admin_client.get(f"{BASE}/{rid}/preview")
    assert resp.status_code == 200
    data = resp.json()
    assert "text" in data
    assert "W0TST" in data["text"]


@pytest.mark.anyio
async def test_update_draft_route(admin_client, db_setup):
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"{BASE}/generate/{sid}")
    rid = gen_resp.json()["id"]
    resp = await admin_client.patch(f"{BASE}/{rid}", json={"content_header": "Edited"})
    assert resp.status_code == 200
    assert resp.json()["content_header"] == "Edited"


@pytest.mark.anyio
async def test_approve_roster_route(admin_client, db_setup):
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"{BASE}/generate/{sid}")
    rid = gen_resp.json()["id"]
    resp = await admin_client.post(f"{BASE}/{rid}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert resp.json()["approved_by"] == "W0NE"


@pytest.mark.anyio
async def test_send_roster_route(admin_client, db_setup):
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"{BASE}/generate/{sid}")
    rid = gen_resp.json()["id"]
    await admin_client.post(f"{BASE}/{rid}/approve")
    with patch(
        "backend.integrations.delivery.service.dispatch_delivery",
        return_value=True,
    ):
        resp = await admin_client.post(f"{BASE}/{rid}/send")
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"


@pytest.mark.anyio
async def test_skip_roster_route(admin_client, db_setup):
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"{BASE}/generate/{sid}")
    rid = gen_resp.json()["id"]
    resp = await admin_client.post(f"{BASE}/{rid}/skip")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


@pytest.mark.anyio
async def test_approve_non_draft_returns_409(admin_client, db_setup):
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"{BASE}/generate/{sid}")
    rid = gen_resp.json()["id"]
    await admin_client.post(f"{BASE}/{rid}/approve")
    resp = await admin_client.post(f"{BASE}/{rid}/approve")
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_regenerate_roster_route_rewrites_draft(admin_client, db_setup):
    """Net control / admin can regenerate a draft from the current state."""
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"{BASE}/generate/{sid}")
    rid = gen_resp.json()["id"]

    with db_setup["factory"]() as session:
        from backend.modules.roster.models import RosterLog

        log = session.get(RosterLog, rid)
        log.content_subject = "Stale subject"
        session.commit()

    resp = await admin_client.post(f"{BASE}/{rid}/regenerate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == rid
    assert data["status"] == "draft"
    assert data["content_subject"] != "Stale subject"


@pytest.mark.anyio
async def test_regenerate_roster_route_404_when_missing(admin_client):
    resp = await admin_client.post(f"{BASE}/9999/regenerate")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_regenerate_roster_route_409_when_not_draft(admin_client, db_setup):
    """Approved rosters can't be regenerated."""
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"{BASE}/generate/{sid}")
    rid = gen_resp.json()["id"]
    await admin_client.post(f"{BASE}/{rid}/approve")

    resp = await admin_client.post(f"{BASE}/{rid}/regenerate")
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_regenerate_roster_route_requires_role(viewer_client, admin_client, db_setup):
    """Viewer cannot regenerate."""
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"{BASE}/generate/{sid}")
    rid = gen_resp.json()["id"]

    resp = await viewer_client.post(f"{BASE}/{rid}/regenerate")
    assert resp.status_code == 403


# --- Cross-net roster rejection ---


@pytest.mark.anyio
async def test_roster_log_cross_net_404(admin_client, db_setup):
    """A roster belonging to a session in another net returns 404."""
    # First generate a roster in the test net
    sid = db_setup["net_session"].id
    gen_resp = await admin_client.post(f"{BASE}/generate/{sid}")
    rid = gen_resp.json()["id"]

    # Now try to access it via a different net slug
    resp = await admin_client.get(f"/api/nets/nonexistent-net/roster/{rid}/preview")
    assert resp.status_code == 404
