"""API tests for schedule routes mounted at /api/nets/{net_slug}/schedule/."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.modules.schedule.routes import schedule_router
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
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
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
        net = Net(slug="t", name="Test Net", is_public=False)
        session.add_all([admin, viewer, net])
        session.flush()
        # Give admin NET_CONTROL membership (is_admin bypasses role checks,
        # but the membership row lets the viewer-role test work too)
        session.add(NetMembership(user_callsign="W0NE", net_id=net.id, role=NetRole.NET_CONTROL))
        session.add(NetMembership(user_callsign="KD0TST", net_id=net.id, role=NetRole.VIEWER))
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    # Router carries its own prefix: /api/nets/{net_slug}/schedule
    app.include_router(schedule_router)
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# Shorthand helpers
SEASONS_URL = "/api/nets/t/schedule/seasons"
SESSIONS_URL = "/api/nets/t/schedule/sessions"


@pytest.mark.asyncio
async def test_create_season(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.post(
        SEASONS_URL,
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "day_of_week": 3,
            "time": "19:00",
            "is_week_long": False,
            "activity_cadence": 2,
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Fall 2026"
    assert data["id"] is not None
    assert len(data["sessions"]) == 5


@pytest.mark.asyncio
async def test_list_seasons(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    # Create a season first
    await test_client.post(
        SEASONS_URL,
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "day_of_week": 3,
            "time": "19:00",
            "is_week_long": False,
            "activity_cadence": 2,
        },
        cookies={"access_token": token},
    )

    response = await test_client.get(SEASONS_URL, cookies={"access_token": token})
    assert response.status_code == 200
    seasons = response.json()
    assert len(seasons) == 1
    assert seasons[0]["name"] == "Fall 2026"


@pytest.mark.asyncio
async def test_session_roster_status_field(test_client, test_settings, db_setup):
    """Frontend's 'don't advance until roster is SENT' logic (backlog item 3)
    needs roster_status surfaced on every session response."""
    from datetime import date, datetime, timezone
    from backend.modules.roster.models import RosterLog, RosterStatus
    from backend.modules.schedule.models import NetSession, SessionType
    from backend.modules.nets.models import Net

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    with db_setup() as session:
        net = session.query(Net).filter_by(slug="t").one()
        from backend.modules.schedule.models import NetSeason
        from datetime import date as d

        season = NetSeason(
            net_id=net.id,
            name="Test Season",
            start_date=d(2026, 4, 1),
            end_date=d(2026, 4, 30),
            day_of_week=3,
            is_week_long=False,
            activity_cadence=2,
        )
        session.add(season)
        session.flush()
        net_session = NetSession(
            season_id=season.id,
            start_date=date(2026, 4, 10),
            end_date=date(2026, 4, 10),
            session_type=SessionType.REGULAR_CHECKIN,
            grace_period_hours=24.0,
        )
        session.add(net_session)
        session.flush()
        roster = RosterLog(
            session_id=net_session.id,
            status=RosterStatus.DRAFT,
            content_subject="subj",
            content_header="h",
            content_welcome="w",
            content_comments="c",
            content_footer="f",
            drafted_at=datetime.now(tz=timezone.utc),
        )
        session.add(roster)
        session.commit()
        session_id = net_session.id

    response = await test_client.get(
        f"/api/nets/t/schedule/sessions/{session_id}",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["roster_status"] == "draft"


@pytest.mark.asyncio
async def test_session_roster_status_null_when_no_roster(test_client, test_settings, db_setup):
    from datetime import date
    from backend.modules.schedule.models import NetSession, NetSeason, SessionType
    from backend.modules.nets.models import Net

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    with db_setup() as session:
        net = session.query(Net).filter_by(slug="t").one()
        from datetime import date as d

        season = NetSeason(
            net_id=net.id,
            name="Test Season",
            start_date=d(2026, 4, 1),
            end_date=d(2026, 4, 30),
            day_of_week=3,
            is_week_long=False,
            activity_cadence=2,
        )
        session.add(season)
        session.flush()
        net_session = NetSession(
            season_id=season.id,
            start_date=date(2026, 4, 10),
            end_date=date(2026, 4, 10),
            session_type=SessionType.REGULAR_CHECKIN,
            grace_period_hours=24.0,
        )
        session.add(net_session)
        session.commit()
        session_id = net_session.id

    response = await test_client.get(
        f"/api/nets/t/schedule/sessions/{session_id}",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["roster_status"] is None


@pytest.mark.asyncio
async def test_list_sessions(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    create_resp = await test_client.post(
        SEASONS_URL,
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "day_of_week": 3,
            "time": "19:00",
            "is_week_long": False,
            "activity_cadence": 2,
        },
        cookies={"access_token": token},
    )
    season_id = create_resp.json()["id"]

    response = await test_client.get(
        f"/api/nets/t/schedule/seasons/{season_id}/sessions",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) == 5


@pytest.mark.asyncio
async def test_update_session(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    create_resp = await test_client.post(
        SEASONS_URL,
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "day_of_week": 3,
            "time": "19:00",
            "is_week_long": False,
            "activity_cadence": 2,
        },
        cookies={"access_token": token},
    )
    session_id = create_resp.json()["sessions"][0]["id"]

    response = await test_client.patch(
        f"/api/nets/t/schedule/sessions/{session_id}",
        json={"status": "cancelled"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_create_adhoc_real_event(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.post(
        SESSIONS_URL,
        json={
            "start_date": "2026-04-15",
            "session_type": "real_event",
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["session_type"] == "real_event"
    assert data["season_id"] is None
    assert data["end_date"] is None
    assert data["status"] == "scheduled"


@pytest.mark.asyncio
async def test_create_real_event_with_season_rejected(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    season_resp = await test_client.post(
        SEASONS_URL,
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "day_of_week": 3,
            "time": "19:00",
        },
        cookies={"access_token": token},
    )
    season_id = season_resp.json()["id"]

    response = await test_client.post(
        SESSIONS_URL,
        json={
            "start_date": "2026-04-15",
            "session_type": "real_event",
            "season_id": season_id,
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 400
    assert "season" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_sessions_with_filters(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)

    season_resp = await test_client.post(
        SEASONS_URL,
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-09-10",
            "day_of_week": 3,
            "time": "19:00",
        },
        cookies={"access_token": token},
    )
    season_id = season_resp.json()["id"]

    # Ad-hoc real event has no season, so it won't appear in per-net listing
    # (no season_id → not attributable to a net via join). Season sessions: 2.
    resp_all = await test_client.get(
        SESSIONS_URL,
        cookies={"access_token": token},
    )
    assert resp_all.status_code == 200
    all_sessions = resp_all.json()
    assert len(all_sessions) == 2  # only the 2 season sessions (ad-hoc excluded)

    resp_season = await test_client.get(
        f"{SESSIONS_URL}?season_id={season_id}",
        cookies={"access_token": token},
    )
    assert resp_season.status_code == 200
    assert len(resp_season.json()) == 2

    resp_status = await test_client.get(
        f"{SESSIONS_URL}?status=scheduled",
        cookies={"access_token": token},
    )
    assert resp_status.status_code == 200
    assert len(resp_status.json()) == 2


@pytest.mark.asyncio
async def test_get_single_session(test_client, test_settings):
    """Session created via a season is accessible by ID on the correct net."""
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    create_resp = await test_client.post(
        SEASONS_URL,
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-09-03",
            "day_of_week": 3,
            "time": "19:00",
        },
        cookies={"access_token": token},
    )
    session_id = create_resp.json()["sessions"][0]["id"]

    response = await test_client.get(
        f"/api/nets/t/schedule/sessions/{session_id}",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == session_id
    assert data["session_type"] == "regular_checkin"
    # The session has a non-null season_id (season-attached path)
    assert data["season_id"] is not None


@pytest.mark.asyncio
async def test_get_session_not_found(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.get(
        "/api/nets/t/schedule/sessions/9999",
        cookies={"access_token": token},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_season_end_before_start_rejected(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.post(
        SEASONS_URL,
        json={
            "name": "Bad Season",
            "start_date": "2026-10-01",
            "end_date": "2026-09-01",
            "day_of_week": 3,
            "time": "19:00",
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_season_no_day_of_week_rejected(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.post(
        SEASONS_URL,
        json={
            "name": "Bad Season",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "is_week_long": False,
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_viewer_can_read_but_not_create(test_client, test_settings):
    admin_token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    viewer_token = make_test_token("KD0TST", test_settings, token_version=0)

    # Create as admin
    await test_client.post(
        SEASONS_URL,
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "day_of_week": 3,
            "time": "19:00",
            "is_week_long": False,
            "activity_cadence": 2,
        },
        cookies={"access_token": admin_token},
    )

    # Viewer can list
    response = await test_client.get(SEASONS_URL, cookies={"access_token": viewer_token})
    assert response.status_code == 200

    # Viewer cannot create (needs NET_CONTROL)
    response = await test_client.post(
        SEASONS_URL,
        json={
            "name": "Hacked Season",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "day_of_week": 0,
            "time": "00:00",
            "is_week_long": False,
            "activity_cadence": 1,
        },
        cookies={"access_token": viewer_token},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_sessions_requires_auth(test_client):
    """Anonymous requests are rejected (401) since the route now requires VIEWER role."""
    resp = await test_client.get(SESSIONS_URL)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_sessions_authenticated_sees_all(test_client, test_settings, db_setup):
    """Authenticated callers see all session statuses for their net."""
    admin_token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)

    # Create a season with multiple sessions
    await test_client.post(
        SEASONS_URL,
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-09-10",
            "day_of_week": 3,
            "time": "19:00",
        },
        cookies={"access_token": admin_token},
    )

    # Authenticated user should see all
    resp = await test_client.get(SESSIONS_URL, cookies={"access_token": admin_token})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 2  # At least some sessions from the season


@pytest.mark.asyncio
async def test_delete_season_preserves_completed_sessions(test_client, test_settings, db_setup):
    """Operator expectation: dropping a season is a planning cleanup, not
    a history wipe. Completed sessions survive (orphaned, season_id=None);
    scheduled and cancelled sessions go with the season."""
    from backend.modules.schedule.models import NetSession, SessionStatus

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)

    season_resp = await test_client.post(
        SEASONS_URL,
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-10-01",
            "day_of_week": 3,
            "time": "19:00",
        },
        cookies={"access_token": token},
    )
    season_id = season_resp.json()["id"]

    with db_setup() as session:
        rows = session.query(NetSession).filter_by(season_id=season_id).order_by(NetSession.start_date).all()
        # First session is the one we ran; mark it completed.
        completed_id = rows[0].id
        rows[0].status = SessionStatus.COMPLETED
        # Second session got cancelled.
        cancelled_id = rows[1].id
        rows[1].status = SessionStatus.CANCELLED
        # Remaining stay scheduled.
        scheduled_ids = {r.id for r in rows[2:]}
        session.commit()

    resp = await test_client.delete(
        f"/api/nets/t/schedule/seasons/{season_id}",
        cookies={"access_token": token},
    )
    assert resp.status_code == 204

    with db_setup() as session:
        # Completed survives, detached.
        survivor = session.query(NetSession).filter_by(id=completed_id).one()
        assert survivor.season_id is None
        assert survivor.status == SessionStatus.COMPLETED
        # Cancelled and scheduled are gone.
        assert session.query(NetSession).filter_by(id=cancelled_id).first() is None
        for sid in scheduled_ids:
            assert session.query(NetSession).filter_by(id=sid).first() is None


# ---------------------------------------------------------------------------
# Cross-net isolation tests (C1)
# ---------------------------------------------------------------------------


def _seed_other_net_session(db_setup):
    """Create a second net 'other' with its own season and session.

    Returns the session ID so tests can attempt cross-net access.
    """
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
    from datetime import date

    with db_setup() as session:
        other_net = Net(slug="other", name="Other Net")
        session.add(other_net)
        session.flush()
        season = NetSeason(
            net_id=other_net.id,
            name="Other Season",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            day_of_week=2,
            is_week_long=False,
            activity_cadence=2,
        )
        session.add(season)
        session.flush()
        net_session = NetSession(
            season_id=season.id,
            start_date=date(2026, 1, 7),
            end_date=date(2026, 1, 7),
            session_type=SessionType.REGULAR_CHECKIN,
            status=SessionStatus.SCHEDULED,
            grace_period_hours=24.0,
        )
        session.add(net_session)
        session.commit()
        return net_session.id


@pytest.mark.asyncio
async def test_get_session_rejects_cross_net(test_client, test_settings, db_setup):
    """GET /sessions/{id} on net 't' must return 404 for a session that belongs
    to a different net, hiding its existence (no 403 leakage)."""
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    other_session_id = _seed_other_net_session(db_setup)

    # Attempt to read the other net's session via net 't'
    resp = await test_client.get(
        f"/api/nets/t/schedule/sessions/{other_session_id}",
        cookies={"access_token": token},
    )
    assert resp.status_code == 404, (
        f"Expected 404 for cross-net session access, got {resp.status_code}"
    )


@pytest.mark.asyncio
async def test_patch_session_rejects_cross_net(test_client, test_settings, db_setup):
    """PATCH /sessions/{id} on net 't' must return 404 for a session that belongs
    to a different net, hiding its existence and preventing cross-net mutation."""
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    other_session_id = _seed_other_net_session(db_setup)

    # Attempt to mutate the other net's session via net 't'
    resp = await test_client.patch(
        f"/api/nets/t/schedule/sessions/{other_session_id}",
        json={"status": "cancelled"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 404, (
        f"Expected 404 for cross-net session mutation, got {resp.status_code}"
    )

    # Verify the session was NOT mutated
    from backend.modules.schedule.models import NetSession, SessionStatus

    with db_setup() as session:
        still_there = session.query(NetSession).filter_by(id=other_session_id).one()
        assert still_there.status == SessionStatus.SCHEDULED
