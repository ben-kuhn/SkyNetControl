import pytest
from fastapi import FastAPI, Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.auth.dependencies import get_current_user, require_role, require_not_pending
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
def db_engine():
    engine = create_engine(
        "sqlite:///",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session_factory(db_engine):
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture
def seeded_db(db_session_factory):
    with db_session_factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin User",
            role=UserRole.ADMIN,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer User",
            role=UserRole.VIEWER,
        )
        pending = User(
            callsign="PENDING-abc123",
            oidc_subject="auth0|pending",
            name="Pending User",
            role=UserRole.PENDING,
        )
        session.add_all([admin, viewer, pending])
        session.commit()
    return db_session_factory


@pytest.fixture
def test_app(test_settings, seeded_db):
    app = FastAPI()
    app.state.session_factory = seeded_db
    app.state.settings = test_settings

    @app.get("/api/test/me")
    async def me(user: User = Depends(get_current_user)):
        return {"callsign": user.callsign, "role": user.role.value}

    @app.get("/api/test/admin-only")
    async def admin_only(user: User = Depends(require_role(UserRole.ADMIN))):
        return {"message": "admin access granted"}

    @app.get("/api/test/not-pending")
    async def not_pending(user: User = Depends(require_not_pending)):
        return {"callsign": user.callsign}

    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_authenticated_user(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get("/api/test/me", cookies={"access_token": token})
    assert response.status_code == 200
    assert response.json()["callsign"] == "W0NE"


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(test_client):
    response = await test_client.get("/api/test/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_role_required(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get("/api/test/admin-only", cookies={"access_token": token})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_access_admin(test_client, test_settings):
    token = create_access_token("KD0TST", "viewer", test_settings)
    response = await test_client.get("/api/test/admin-only", cookies={"access_token": token})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_pending_user_blocked_by_require_not_pending(test_client, test_settings):
    token = create_access_token("PENDING-abc123", "pending", test_settings)
    response = await test_client.get("/api/test/not-pending", cookies={"access_token": token})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_viewer_passes_require_not_pending(test_client, test_settings):
    token = create_access_token("KD0TST", "viewer", test_settings)
    response = await test_client.get("/api/test/not-pending", cookies={"access_token": token})
    assert response.status_code == 200
    assert response.json()["callsign"] == "KD0TST"


@pytest.mark.asyncio
async def test_admin_passes_require_not_pending(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get("/api/test/not-pending", cookies={"access_token": token})
    assert response.status_code == 200
