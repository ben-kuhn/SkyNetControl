import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.auth.service import create_access_token
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
        session.add_all([admin, viewer])
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(schedule_router, prefix="/api/schedule")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_season(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.post(
        "/api/schedule/seasons",
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
        "/api/schedule/seasons",
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

    response = await test_client.get("/api/schedule/seasons", cookies={"access_token": token})
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

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    with db_setup() as session:
        net_session = NetSession(
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
        f"/api/schedule/sessions/{session_id}",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["roster_status"] == "draft"


@pytest.mark.asyncio
async def test_session_roster_status_null_when_no_roster(test_client, test_settings, db_setup):
    from datetime import date
    from backend.modules.schedule.models import NetSession, SessionType

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    with db_setup() as session:
        net_session = NetSession(
            start_date=date(2026, 4, 10),
            end_date=date(2026, 4, 10),
            session_type=SessionType.REGULAR_CHECKIN,
            grace_period_hours=24.0,
        )
        session.add(net_session)
        session.commit()
        session_id = net_session.id

    response = await test_client.get(
        f"/api/schedule/sessions/{session_id}",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["roster_status"] is None


@pytest.mark.asyncio
async def test_list_sessions(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    create_resp = await test_client.post(
        "/api/schedule/seasons",
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
        f"/api/schedule/seasons/{season_id}/sessions",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) == 5


@pytest.mark.asyncio
async def test_update_session(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    create_resp = await test_client.post(
        "/api/schedule/seasons",
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
        f"/api/schedule/sessions/{session_id}",
        json={"status": "cancelled"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_create_adhoc_real_event(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.post(
        "/api/schedule/sessions",
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
        "/api/schedule/seasons",
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
        "/api/schedule/sessions",
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
        "/api/schedule/seasons",
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

    await test_client.post(
        "/api/schedule/sessions",
        json={
            "start_date": "2026-04-15",
            "session_type": "real_event",
        },
        cookies={"access_token": token},
    )

    resp_all = await test_client.get(
        "/api/schedule/sessions",
        cookies={"access_token": token},
    )
    assert resp_all.status_code == 200
    all_sessions = resp_all.json()
    assert len(all_sessions) == 3

    resp_season = await test_client.get(
        f"/api/schedule/sessions?season_id={season_id}",
        cookies={"access_token": token},
    )
    assert resp_season.status_code == 200
    assert len(resp_season.json()) == 2

    resp_status = await test_client.get(
        "/api/schedule/sessions?status=scheduled",
        cookies={"access_token": token},
    )
    assert resp_status.status_code == 200
    assert len(resp_status.json()) == 3


@pytest.mark.asyncio
async def test_get_single_session(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    create_resp = await test_client.post(
        "/api/schedule/sessions",
        json={
            "start_date": "2026-04-15",
            "session_type": "real_event",
            "net_control_callsign": "W0NE",
        },
        cookies={"access_token": token},
    )
    session_id = create_resp.json()["id"]

    response = await test_client.get(
        f"/api/schedule/sessions/{session_id}",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == session_id
    assert data["session_type"] == "real_event"


@pytest.mark.asyncio
async def test_get_session_not_found(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.get(
        "/api/schedule/sessions/9999",
        cookies={"access_token": token},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_season_end_before_start_rejected(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.post(
        "/api/schedule/seasons",
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
        "/api/schedule/seasons",
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
        "/api/schedule/seasons",
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
    response = await test_client.get("/api/schedule/seasons", cookies={"access_token": viewer_token})
    assert response.status_code == 200

    # Viewer cannot create
    response = await test_client.post(
        "/api/schedule/seasons",
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
async def test_list_sessions_public_completed_only(test_client, test_settings, db_setup):
    """Anonymous viewers only see COMPLETED sessions."""
    from backend.modules.schedule.models import SessionStatus

    admin_token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)

    # Create a season with multiple sessions
    season_resp = await test_client.post(
        "/api/schedule/seasons",
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-09-10",
            "day_of_week": 3,
            "time": "19:00",
        },
        cookies={"access_token": admin_token},
    )
    season_id = season_resp.json()["id"]

    # Get the created sessions and mark some as completed
    with db_setup() as session:
        from backend.modules.schedule.models import NetSession

        sessions = session.query(NetSession).filter_by(season_id=season_id).all()
        if len(sessions) >= 2:
            sessions[0].status = SessionStatus.COMPLETED
            sessions[1].status = SessionStatus.SCHEDULED
            session.commit()

    # Anonymous call should only see COMPLETED
    resp = await test_client.get("/api/schedule/sessions")
    assert resp.status_code == 200
    body = resp.json()
    # Should see at least one completed session from the season
    completed = [s for s in body if s["status"] == "completed"]
    assert len(completed) >= 1
    # Should not see scheduled sessions
    scheduled = [s for s in body if s["status"] == "scheduled"]
    assert len(scheduled) == 0


@pytest.mark.asyncio
async def test_list_sessions_authenticated_sees_all(test_client, test_settings, db_setup):
    """Authenticated callers see all session statuses."""
    admin_token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)

    # Create a season with multiple sessions
    season_resp = await test_client.post(
        "/api/schedule/seasons",
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-09-10",
            "day_of_week": 3,
            "time": "19:00",
        },
        cookies={"access_token": admin_token},
    )
    season_id = season_resp.json()["id"]

    # Authenticated user should see all
    resp = await test_client.get("/api/schedule/sessions", cookies={"access_token": admin_token})
    assert resp.status_code == 200
    body = resp.json()
    # Authenticated users are not constrained to completed
    assert len(body) >= 2  # At least some sessions from the season


@pytest.mark.asyncio
async def test_list_sessions_pending_user_sees_only_completed(test_client, test_settings, db_setup):
    """Audit M3: PENDING users are treated like anonymous viewers."""
    from backend.modules.schedule.models import NetSession, SessionStatus

    admin_token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)

    # Create a season with sessions, mark statuses to differentiate
    season_resp = await test_client.post(
        "/api/schedule/seasons",
        json={
            "name": "Fall 2026",
            "start_date": "2026-09-03",
            "end_date": "2026-09-10",
            "day_of_week": 3,
            "time": "19:00",
        },
        cookies={"access_token": admin_token},
    )
    season_id = season_resp.json()["id"]

    with db_setup() as session:
        sessions = session.query(NetSession).filter_by(season_id=season_id).all()
        if len(sessions) >= 2:
            sessions[0].status = SessionStatus.COMPLETED
            sessions[1].status = SessionStatus.SCHEDULED
        # Seed a PENDING user
        session.add(
            User(
                callsign="PENDING-y",
                oidc_subject="auth0|pendingy",
                name="Pending",
                is_pending=True,
            )
        )
        session.commit()

    pending_token = make_test_token("PENDING-y", test_settings, is_pending=True, token_version=0)
    resp = await test_client.get(
        "/api/schedule/sessions",
        cookies={"access_token": pending_token},
    )
    assert resp.status_code == 200
    body = resp.json()
    scheduled = [s for s in body if s["status"] == "scheduled"]
    assert len(scheduled) == 0


@pytest.mark.asyncio
async def test_delete_season_preserves_completed_sessions(test_client, test_settings, db_setup):
    """Operator expectation: dropping a season is a planning cleanup, not
    a history wipe. Completed sessions survive (orphaned, season_id=None);
    scheduled and cancelled sessions go with the season."""
    from backend.modules.schedule.models import NetSession, SessionStatus

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)

    season_resp = await test_client.post(
        "/api/schedule/seasons",
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
        f"/api/schedule/seasons/{season_id}",
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
