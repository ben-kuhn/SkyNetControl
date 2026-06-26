import pytest
from unittest.mock import MagicMock, patch
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.config_mgmt.models import AppConfig
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.config import Settings
from tests.conftest import make_test_token

NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/activities"


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
        net = Net(slug=NET_SLUG, name="Test Net")
        session.add_all([admin, net])
        session.flush()

        # Admin is global admin, no net membership needed
        # Seed Claude API key in AppConfig
        config = AppConfig(key="claude_api_key", value="test-key-123")
        session.add(config)
        session.commit()
    return {"engine": engine, "factory": factory}


@pytest.fixture
def app(test_settings, db_setup):
    from backend.app import create_app

    application = create_app(settings=test_settings)
    application.state.engine = db_setup["engine"]
    application.state.session_factory = db_setup["factory"]
    Base.metadata.create_all(db_setup["engine"])
    return application


@pytest.fixture
async def test_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin_client(app, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token}) as c:
        yield c


@pytest.mark.asyncio
async def test_create_chat_session(admin_client):
    response = await admin_client.post(BASE + "/chat/sessions")
    assert response.status_code == 201
    data = response.json()
    assert data["id"] is not None
    assert data["activity_id"] is None
    assert data["messages"] == []


@pytest.mark.asyncio
async def test_send_chat_message(admin_client):
    create_resp = await admin_client.post(BASE + "/chat/sessions")
    chat_id = create_resp.json()["id"]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Great idea! How about a simplex exercise?")]

    with patch("backend.modules.activities.chat_service._call_claude") as mock_claude:
        mock_claude.return_value = mock_response
        response = await admin_client.post(
            f"{BASE}/chat/sessions/{chat_id}/messages",
            json={"content": "I want an HF activity"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["user_message"]["content"] == "I want an HF activity"
    assert "simplex exercise" in data["assistant_message"]["content"]


@pytest.mark.asyncio
async def test_get_chat_session_with_messages(admin_client):
    create_resp = await admin_client.post(BASE + "/chat/sessions")
    chat_id = create_resp.json()["id"]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Sure, here's an idea")]

    with patch("backend.modules.activities.chat_service._call_claude") as mock_claude:
        mock_claude.return_value = mock_response
        await admin_client.post(
            f"{BASE}/chat/sessions/{chat_id}/messages",
            json={"content": "Hello"},
        )

    response = await admin_client.get(f"{BASE}/chat/sessions/{chat_id}")
    assert response.status_code == 200
    data = response.json()
    assert len(data["messages"]) == 2


@pytest.mark.asyncio
async def test_approve_chat_creates_activity(admin_client):
    create_resp = await admin_client.post(BASE + "/chat/sessions")
    chat_id = create_resp.json()["id"]

    response = await admin_client.post(
        f"{BASE}/chat/sessions/{chat_id}/approve",
        json={
            "title": "Emergency Prep",
            "description": "Practice emergency comms",
            "instructions": "Set up your go-kit",
            "tag_names": ["emergency-prep"],
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Emergency Prep"
    assert data["id"] is not None

    # Verify chat session is linked to activity
    chat_resp = await admin_client.get(f"{BASE}/chat/sessions/{chat_id}")
    assert chat_resp.json()["activity_id"] == data["id"]


@pytest.mark.asyncio
async def test_send_message_without_api_key(admin_client, db_setup):
    # Remove the API key from config
    with db_setup["factory"]() as session:
        config = session.get(AppConfig, "claude_api_key")
        if config:
            session.delete(config)
            session.commit()

    create_resp = await admin_client.post(BASE + "/chat/sessions")
    chat_id = create_resp.json()["id"]

    response = await admin_client.post(
        f"{BASE}/chat/sessions/{chat_id}/messages",
        json={"content": "Hello"},
    )
    assert response.status_code == 503
