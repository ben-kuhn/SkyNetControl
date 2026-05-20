from backend.auth.models import UserRole


def test_deleted_role_exists():
    assert UserRole.DELETED == "deleted"
    assert "deleted" in [r.value for r in UserRole]


import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.auth.dependencies import get_current_user
from backend.config import Settings


@pytest.fixture
def privacy_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )


@pytest.fixture
def privacy_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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
            email="viewer@example.com",
        )
        deleted = User(
            callsign="ANON-AAAA",
            oidc_subject="deleted",
            name="Deleted User",
            role=UserRole.DELETED,
        )
        session.add_all([admin, viewer, deleted])
        session.commit()
    return factory


@pytest.fixture
def auth_app(privacy_settings, privacy_db):
    app = FastAPI()
    app.state.session_factory = privacy_db
    app.state.settings = privacy_settings

    @app.get("/me")
    async def me(user: User = Depends(get_current_user)):
        return {"callsign": user.callsign}

    return app


@pytest.fixture
async def auth_client(auth_app):
    transport = ASGITransport(app=auth_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_deleted_user_cannot_authenticate(auth_client, privacy_settings):
    token = create_access_token("ANON-AAAA", "deleted", privacy_settings)
    response = await auth_client.get("/me", cookies={"access_token": token})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_deleted_user_pat_bearer_returns_401(auth_client, privacy_db):
    from backend.auth.pat_models import PersonalAccessToken
    import hashlib, secrets
    raw = "skynet_" + secrets.token_hex(32)
    with privacy_db() as session:
        pat = PersonalAccessToken(
            user_callsign="ANON-AAAA",
            name="Deleted user token",
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            token_prefix=raw[:8],
            scopes="schedule:read",
        )
        session.add(pat)
        session.commit()
    response = await auth_client.get(
        "/me",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 401
