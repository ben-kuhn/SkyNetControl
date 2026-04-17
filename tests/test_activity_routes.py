import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.modules.activities.routes import activities_router
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
        "sqlite://", poolclass=StaticPool,
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
        session.add_all([admin, viewer])
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(activities_router, prefix="/api/activities")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_activity(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/activities/",
        json={
            "title": "Simplex Exercise",
            "description": "Practice simplex",
            "instructions": "Tune to 7.185 MHz",
            "tag_names": ["HF", "beginner-friendly"],
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Simplex Exercise"
    assert data["id"] is not None
    assert len(data["tags"]) == 2


@pytest.mark.asyncio
async def test_list_activities(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    await test_client.post(
        "/api/activities/",
        json={
            "title": "Activity 1",
            "description": "d",
            "instructions": "i",
        },
        cookies={"access_token": token},
    )

    response = await test_client.get(
        "/api/activities/", cookies={"access_token": token}
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_get_activity(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    create_resp = await test_client.post(
        "/api/activities/",
        json={"title": "Find Me", "description": "d", "instructions": "i"},
        cookies={"access_token": token},
    )
    activity_id = create_resp.json()["id"]

    response = await test_client.get(
        f"/api/activities/{activity_id}", cookies={"access_token": token}
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Find Me"


@pytest.mark.asyncio
async def test_update_activity(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    create_resp = await test_client.post(
        "/api/activities/",
        json={"title": "Old", "description": "old", "instructions": "old"},
        cookies={"access_token": token},
    )
    activity_id = create_resp.json()["id"]

    response = await test_client.patch(
        f"/api/activities/{activity_id}",
        json={"title": "New", "tag_names": ["VHF"]},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "New"
    assert len(response.json()["tags"]) == 1


@pytest.mark.asyncio
async def test_delete_activity(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    create_resp = await test_client.post(
        "/api/activities/",
        json={"title": "Delete Me", "description": "d", "instructions": "i"},
        cookies={"access_token": token},
    )
    activity_id = create_resp.json()["id"]

    response = await test_client.delete(
        f"/api/activities/{activity_id}", cookies={"access_token": token}
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_cannot_delete_default_activity(test_client, test_settings, db_setup):
    # Seed a default activity directly
    from backend.modules.activities.models import Activity
    with db_setup() as session:
        activity = Activity(
            title="Default",
            description="Default check-in",
            instructions="Check in",
            is_default=True,
        )
        session.add(activity)
        session.commit()
        activity_id = activity.id

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.delete(
        f"/api/activities/{activity_id}", cookies={"access_token": token}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_read_but_not_create(test_client, test_settings):
    admin_token = create_access_token("W0NE", "admin", test_settings)
    viewer_token = create_access_token("KD0TST", "viewer", test_settings)

    # Create as admin
    await test_client.post(
        "/api/activities/",
        json={"title": "Activity", "description": "d", "instructions": "i"},
        cookies={"access_token": admin_token},
    )

    # Viewer can list
    response = await test_client.get(
        "/api/activities/", cookies={"access_token": viewer_token}
    )
    assert response.status_code == 200

    # Viewer cannot create
    response = await test_client.post(
        "/api/activities/",
        json={"title": "Hack", "description": "d", "instructions": "i"},
        cookies={"access_token": viewer_token},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_tags(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    await test_client.post(
        "/api/activities/",
        json={
            "title": "Tagged",
            "description": "d",
            "instructions": "i",
            "tag_names": ["HF", "VHF"],
        },
        cookies={"access_token": token},
    )

    response = await test_client.get(
        "/api/activities/tags", cookies={"access_token": token}
    )
    assert response.status_code == 200
    tags = response.json()
    assert len(tags) == 2
