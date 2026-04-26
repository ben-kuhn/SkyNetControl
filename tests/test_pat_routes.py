import pytest
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.auth.pat_service import create_token
from backend.auth.pat_routes import pat_router
from backend.config import Settings


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
            callsign="PENDING-abc",
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
    app.include_router(pat_router, prefix="/api/auth/tokens")
    return app


@pytest.fixture
async def client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _admin_cookie(test_settings):
    return {"access_token": create_access_token("W0NE", "admin", test_settings)}


def _viewer_cookie(test_settings):
    return {"access_token": create_access_token("KD0TST", "viewer", test_settings)}


def _pending_cookie(test_settings):
    return {"access_token": create_access_token("PENDING-abc", "pending", test_settings)}


@pytest.mark.asyncio
async def test_create_token_success(client, test_settings):
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "My token", "scopes": ["schedule:read"]},
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "My token"
    assert data["token"].startswith("skynet_")
    assert data["scopes"] == ["schedule:read"]
    assert data["token_prefix"] == data["token"][:8]


@pytest.mark.asyncio
async def test_create_token_with_expiry(client, test_settings):
    future = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "Expiring", "scopes": ["schedule:read"], "expires_at": future},
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 201
    assert response.json()["expires_at"] is not None


@pytest.mark.asyncio
async def test_create_token_invalid_scope_for_viewer(client, test_settings):
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "Bad scope", "scopes": ["users:write"]},
        cookies=_viewer_cookie(test_settings),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_token_pending_user_blocked(client, test_settings):
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "No tokens", "scopes": ["schedule:read"]},
        cookies=_pending_cookie(test_settings),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_token_empty_name(client, test_settings):
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "", "scopes": ["schedule:read"]},
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_token_empty_scopes(client, test_settings):
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "No scopes", "scopes": []},
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_token_past_expiry(client, test_settings):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "Past", "scopes": ["schedule:read"], "expires_at": past},
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_token_cannot_use_pat(client, test_settings, seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", UserRole.ADMIN, "Boot", ["schedule:read"], None)
        raw = result["token"]
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "Via PAT", "scopes": ["schedule:read"]},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_tokens(client, test_settings):
    await client.post(
        "/api/auth/tokens",
        json={"name": "Listed", "scopes": ["schedule:read"]},
        cookies=_admin_cookie(test_settings),
    )
    response = await client.get(
        "/api/auth/tokens",
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Listed"
    assert "token" not in data[0]


@pytest.mark.asyncio
async def test_revoke_token(client, test_settings):
    create_resp = await client.post(
        "/api/auth/tokens",
        json={"name": "To revoke", "scopes": ["schedule:read"]},
        cookies=_admin_cookie(test_settings),
    )
    token_id = create_resp.json()["id"]
    response = await client.delete(
        f"/api/auth/tokens/{token_id}",
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 204
    list_resp = await client.get(
        "/api/auth/tokens",
        cookies=_admin_cookie(test_settings),
    )
    assert len(list_resp.json()) == 0


@pytest.mark.asyncio
async def test_revoke_token_not_found(client, test_settings):
    response = await client.delete(
        "/api/auth/tokens/99999",
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_can_revoke_others_token(client, test_settings):
    create_resp = await client.post(
        "/api/auth/tokens",
        json={"name": "Viewer token", "scopes": ["schedule:read"]},
        cookies=_viewer_cookie(test_settings),
    )
    token_id = create_resp.json()["id"]
    response = await client.delete(
        f"/api/auth/tokens/{token_id}",
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_non_owner_non_admin_cannot_revoke(client, test_settings):
    create_resp = await client.post(
        "/api/auth/tokens",
        json={"name": "Admin token", "scopes": ["schedule:read"]},
        cookies=_admin_cookie(test_settings),
    )
    token_id = create_resp.json()["id"]
    response = await client.delete(
        f"/api/auth/tokens/{token_id}",
        cookies=_viewer_cookie(test_settings),
    )
    assert response.status_code == 404
