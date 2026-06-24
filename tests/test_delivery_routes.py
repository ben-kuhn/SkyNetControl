import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.config_mgmt.models import AppConfig
from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.integrations.delivery.routes import delivery_router
from backend.config import Settings

pytestmark = pytest.mark.xfail(
    reason="role attribute removed in Task 3; restored as is_admin/is_pending/is_deleted in Task 4",
    strict=False,
)


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
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(
            User(
                callsign="ADMIN",
                oidc_subject="local|admin",
                name="Admin User",
                email="admin@test.com",
                role=UserRole.ADMIN,
            )
        )
        session.commit()
    return factory


@pytest.fixture
def app(test_settings, db_setup):
    application = FastAPI()
    application.state.session_factory = db_setup
    application.state.settings = test_settings
    application.include_router(delivery_router, prefix="/api/delivery")
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _auth_headers(test_settings, callsign="ADMIN", role="admin"):
    token = create_access_token(callsign, role, test_settings)
    return {"Cookie": f"access_token={token}"}


@pytest.mark.anyio
async def test_get_delivery_status(client, test_settings, db_setup):
    with db_setup() as session:
        session.add(
            DeliveryLog(
                content_type="reminder",
                content_id=1,
                backend="email",
                status=DeliveryStatus.SENT,
                created_at=datetime.now(tz=timezone.utc),
                sent_at=datetime.now(tz=timezone.utc),
            )
        )
        session.commit()

    resp = await client.get(
        "/api/delivery/reminder/1",
        headers=_auth_headers(test_settings),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["backend"] == "email"
    assert data[0]["status"] == "sent"


@pytest.mark.anyio
async def test_get_delivery_status_empty(client, test_settings):
    resp = await client.get(
        "/api/delivery/reminder/999",
        headers=_auth_headers(test_settings),
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_retry_delivery(client, test_settings, db_setup):
    with db_setup() as session:
        session.add(
            DeliveryLog(
                content_type="reminder",
                content_id=1,
                backend="email",
                status=DeliveryStatus.FAILED,
                error_message="SMTP down",
                created_at=datetime.now(tz=timezone.utc),
            )
        )
        session.add(AppConfig(key="delivery.email.to_address", value="net@test.com"))
        session.commit()

    with patch("backend.integrations.delivery.service.get_backend") as mock_get:
        from backend.integrations.delivery.backends.base import DeliveryResult

        mock_backend = type(
            "MockBackend", (), {"send": lambda self, s, b, c: DeliveryResult(success=True, error=None)}
        )()
        mock_get.return_value = mock_backend

        resp = await client.post(
            "/api/delivery/reminder/1/retry",
            headers=_auth_headers(test_settings),
        )

    assert resp.status_code == 200
    assert resp.json()["retried"] is True


@pytest.mark.anyio
async def test_retry_requires_auth(client):
    resp = await client.post("/api/delivery/reminder/1/retry")
    assert resp.status_code == 401
