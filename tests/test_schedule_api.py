import pytest
from datetime import date
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.modules.schedule.routes import schedule_router
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
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
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
    token = create_access_token("W0NE", "admin", test_settings)
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
    token = create_access_token("W0NE", "admin", test_settings)
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

    response = await test_client.get(
        "/api/schedule/seasons", cookies={"access_token": token}
    )
    assert response.status_code == 200
    seasons = response.json()
    assert len(seasons) == 1
    assert seasons[0]["name"] == "Fall 2026"


@pytest.mark.asyncio
async def test_list_sessions(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
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
    token = create_access_token("W0NE", "admin", test_settings)
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
    token = create_access_token("W0NE", "admin", test_settings)
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
    token = create_access_token("W0NE", "admin", test_settings)
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
    token = create_access_token("W0NE", "admin", test_settings)

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
    token = create_access_token("W0NE", "admin", test_settings)
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
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/schedule/sessions/9999",
        cookies={"access_token": token},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_season_end_before_start_rejected(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
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
    token = create_access_token("W0NE", "admin", test_settings)
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
    admin_token = create_access_token("W0NE", "admin", test_settings)
    viewer_token = create_access_token("KD0TST", "viewer", test_settings)

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
    response = await test_client.get(
        "/api/schedule/seasons", cookies={"access_token": viewer_token}
    )
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
