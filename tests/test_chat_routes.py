import pytest
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.config_mgmt.models import AppConfig
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
        session.add(admin)
        # Seed Claude API key in AppConfig
        config = AppConfig(key="claude_api_key", value="test-key-123")
        session.add(config)
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
async def test_create_chat_session(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post(
        "/api/activities/chat/sessions",
        cookies={"access_token": token},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["id"] is not None
    assert data["activity_id"] is None
    assert data["messages"] == []


@pytest.mark.asyncio
async def test_send_chat_message(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)

    # Create session
    create_resp = await test_client.post(
        "/api/activities/chat/sessions",
        cookies={"access_token": token},
    )
    chat_id = create_resp.json()["id"]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Great idea! How about a simplex exercise?")]

    with patch("backend.modules.activities.chat_service._call_claude") as mock_claude:
        mock_claude.return_value = mock_response
        response = await test_client.post(
            f"/api/activities/chat/sessions/{chat_id}/messages",
            json={"content": "I want an HF activity"},
            cookies={"access_token": token},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["user_message"]["content"] == "I want an HF activity"
    assert "simplex exercise" in data["assistant_message"]["content"]


@pytest.mark.asyncio
async def test_get_chat_session_with_messages(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)

    create_resp = await test_client.post(
        "/api/activities/chat/sessions",
        cookies={"access_token": token},
    )
    chat_id = create_resp.json()["id"]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Sure, here's an idea")]

    with patch("backend.modules.activities.chat_service._call_claude") as mock_claude:
        mock_claude.return_value = mock_response
        await test_client.post(
            f"/api/activities/chat/sessions/{chat_id}/messages",
            json={"content": "Hello"},
            cookies={"access_token": token},
        )

    response = await test_client.get(
        f"/api/activities/chat/sessions/{chat_id}",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["messages"]) == 2


@pytest.mark.asyncio
async def test_approve_chat_creates_activity(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)

    create_resp = await test_client.post(
        "/api/activities/chat/sessions",
        cookies={"access_token": token},
    )
    chat_id = create_resp.json()["id"]

    response = await test_client.post(
        f"/api/activities/chat/sessions/{chat_id}/approve",
        json={
            "title": "Emergency Prep",
            "description": "Practice emergency comms",
            "instructions": "Set up your go-kit",
            "tag_names": ["emergency-prep"],
        },
        cookies={"access_token": token},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Emergency Prep"
    assert data["id"] is not None

    # Verify chat session is linked to activity
    chat_resp = await test_client.get(
        f"/api/activities/chat/sessions/{chat_id}",
        cookies={"access_token": token},
    )
    assert chat_resp.json()["activity_id"] == data["id"]


@pytest.mark.asyncio
async def test_send_message_without_api_key(test_client, test_settings, db_setup):
    # Remove the API key from config
    with db_setup() as session:
        config = session.get(AppConfig, "claude_api_key")
        if config:
            session.delete(config)
            session.commit()

    token = create_access_token("W0NE", "admin", test_settings)

    create_resp = await test_client.post(
        "/api/activities/chat/sessions",
        cookies={"access_token": token},
    )
    chat_id = create_resp.json()["id"]

    response = await test_client.post(
        f"/api/activities/chat/sessions/{chat_id}/messages",
        json={"content": "Hello"},
        cookies={"access_token": token},
    )
    assert response.status_code == 503
