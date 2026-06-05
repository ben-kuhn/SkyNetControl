import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.routes import auth_router
from backend.auth.service import create_access_token
from backend.config import Settings, ProviderSettings


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
        auth_google=ProviderSettings(enabled=True, client_id="gid", client_secret="gsec"),
        app_base_url="http://localhost:8000",
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
    return engine, factory


@pytest.fixture
def test_app(test_settings, db_setup):
    engine, factory = db_setup
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = test_settings
    app.state.providers = {}
    app.include_router(auth_router, prefix="/api/auth")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- Registration tests ---


@pytest.mark.asyncio
async def test_register_valid_callsign(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(
            User(
                callsign="PENDING-google12",
                oidc_subject="google:123",
                name="New User",
                role=UserRole.PENDING,
            )
        )
        session.commit()

    token = create_access_token("PENDING-google12", "pending", test_settings)
    response = await test_client.post(
        "/api/auth/register",
        json={"callsign": "W0ABC"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["callsign"] == "W0ABC"
    assert data["role"] == "pending"


@pytest.mark.asyncio
async def test_register_invalid_callsign_format(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(
            User(
                callsign="PENDING-google12",
                oidc_subject="google:123",
                name="New User",
                role=UserRole.PENDING,
            )
        )
        session.commit()

    token = create_access_token("PENDING-google12", "pending", test_settings)
    response = await test_client.post(
        "/api/auth/register",
        json={"callsign": "not-a-callsign"},
        cookies={"access_token": token},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_register_duplicate_callsign(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="google:existing", name="Existing", role=UserRole.ADMIN))
        session.add(
            User(callsign="PENDING-google12", oidc_subject="google:new", name="New User", role=UserRole.PENDING)
        )
        session.commit()

    token = create_access_token("PENDING-google12", "pending", test_settings)
    response = await test_client.post(
        "/api/auth/register",
        json={"callsign": "W0NE"},
        cookies={"access_token": token},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_register_already_registered(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="google:existing", name="Existing", role=UserRole.VIEWER))
        session.commit()

    token = create_access_token("W0NE", "viewer", test_settings)
    response = await test_client.post(
        "/api/auth/register",
        json={"callsign": "W0NEW"},
        cookies={"access_token": token},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_register_admin_with_placeholder_callsign(test_client, test_settings, db_setup):
    """First-signup admins start with a PENDING-... placeholder callsign and must be
    able to claim a real one via /register without going through the
    admin-approval round-trip."""
    _, factory = db_setup
    with factory() as session:
        session.add(
            User(
                callsign="PENDING-pocketid:80f",
                oidc_subject="pocketid:80f1abc",
                name="First Admin",
                role=UserRole.ADMIN,
            )
        )
        session.commit()

    token = create_access_token("PENDING-pocketid:80f", "admin", test_settings)
    response = await test_client.post(
        "/api/auth/register",
        json={"callsign": "W0ABC"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["callsign"] == "W0ABC"
    assert body["role"] == "admin"


@pytest.mark.asyncio
async def test_register_unauthenticated(test_client):
    response = await test_client.post("/api/auth/register", json={"callsign": "W0ABC"})
    assert response.status_code == 401


# --- Callsign change request tests ---


@pytest.mark.asyncio
async def test_request_callsign_change(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0OLD", oidc_subject="google:user1", name="User One", role=UserRole.VIEWER))
        session.commit()

    token = create_access_token("W0OLD", "viewer", test_settings)
    response = await test_client.patch(
        "/api/auth/me",
        json={"callsign": "W0NEW"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["pending_callsign"] == "W0NEW"

    with factory() as session:
        user = session.get(User, "W0OLD")
        assert user is not None
        assert user.pending_callsign == "W0NEW"


@pytest.mark.asyncio
async def test_request_callsign_change_invalid_format(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0OLD", oidc_subject="google:user1", name="User One", role=UserRole.VIEWER))
        session.commit()

    token = create_access_token("W0OLD", "viewer", test_settings)
    response = await test_client.patch("/api/auth/me", json={"callsign": "invalid"}, cookies={"access_token": token})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_request_callsign_change_taken(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0OLD", oidc_subject="google:u1", name="User", role=UserRole.VIEWER))
        session.add(User(callsign="W0NEW", oidc_subject="google:u2", name="Other", role=UserRole.VIEWER))
        session.commit()

    token = create_access_token("W0OLD", "viewer", test_settings)
    response = await test_client.patch("/api/auth/me", json={"callsign": "W0NEW"}, cookies={"access_token": token})
    assert response.status_code == 409


# --- Callsign approval tests ---


@pytest.mark.asyncio
async def test_approve_callsign_change(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="google:admin", name="Admin", role=UserRole.ADMIN))
        session.add(
            User(
                callsign="W0OLD",
                oidc_subject="google:user1",
                name="User",
                role=UserRole.VIEWER,
                pending_callsign="W0NEW",
            )
        )
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post("/api/auth/users/W0OLD/approve-callsign", cookies={"access_token": token})
    assert response.status_code == 200
    data = response.json()
    assert data["callsign"] == "W0NEW"
    assert data["pending_callsign"] is None


@pytest.mark.asyncio
async def test_approve_callsign_no_pending(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="google:admin", name="Admin", role=UserRole.ADMIN))
        session.add(User(callsign="W0OLD", oidc_subject="google:u1", name="User", role=UserRole.VIEWER))
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.post("/api/auth/users/W0OLD/approve-callsign", cookies={"access_token": token})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_reject_callsign_change(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="google:admin", name="Admin", role=UserRole.ADMIN))
        session.add(
            User(
                callsign="W0OLD",
                oidc_subject="google:u1",
                name="User",
                role=UserRole.VIEWER,
                pending_callsign="W0NEW",
            )
        )
        session.commit()

    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.delete("/api/auth/users/W0OLD/pending-callsign", cookies={"access_token": token})
    assert response.status_code == 200

    with factory() as session:
        user = session.get(User, "W0OLD")
        assert user.pending_callsign is None


@pytest.mark.asyncio
async def test_viewer_cannot_approve_callsign(test_client, test_settings, db_setup):
    _, factory = db_setup
    with factory() as session:
        session.add(User(callsign="W0NE", oidc_subject="google:admin", name="Admin", role=UserRole.ADMIN))
        session.add(
            User(
                callsign="KD0TST",
                oidc_subject="google:viewer",
                name="Viewer",
                role=UserRole.VIEWER,
                pending_callsign="W0NEW",
            )
        )
        session.commit()

    token = create_access_token("KD0TST", "viewer", test_settings)
    response = await test_client.post("/api/auth/users/KD0TST/approve-callsign", cookies={"access_token": token})
    assert response.status_code == 403
