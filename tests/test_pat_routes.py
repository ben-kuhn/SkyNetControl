import pytest
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.auth.pat_service import create_token
from backend.auth.pat_routes import pat_router
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
            is_admin=True,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer User",
            
        )
        pending = User(
            callsign="PENDING-abc",
            oidc_subject="auth0|pending",
            name="Pending User",
            is_pending=True,
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
    return {"access_token": make_test_token("W0NE", test_settings, is_admin=True, token_version=0)}


def _viewer_cookie(test_settings):
    return {"access_token": make_test_token("KD0TST", test_settings, token_version=0)}


def _pending_cookie(test_settings):
    return {"access_token": make_test_token("PENDING-abc", test_settings, is_pending=True, token_version=0)}


@pytest.mark.asyncio
async def test_create_token_success(client, test_settings):
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "My token", "scopes": ["schedule:read"], "net_id": 1},
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
        json={"name": "Expiring", "scopes": ["schedule:read"], "expires_at": future, "net_id": 1},
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
        json={"name": "No tokens", "scopes": ["schedule:read"], "net_id": 1},
        cookies=_pending_cookie(test_settings),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_token_empty_name(client, test_settings):
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "", "scopes": ["schedule:read"], "net_id": 1},
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
        json={"name": "Past", "scopes": ["schedule:read"], "expires_at": past, "net_id": 1},
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_token_cannot_use_pat(client, test_settings, seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", True, "Boot", ["schedule:read"], None, net_id=1)
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
        json={"name": "Listed", "scopes": ["schedule:read"], "net_id": 1},
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
        json={"name": "To revoke", "scopes": ["schedule:read"], "net_id": 1},
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
        json={"name": "Viewer token", "scopes": ["schedule:read"], "net_id": 1},
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
        json={"name": "Admin token", "scopes": ["schedule:read"], "net_id": 1},
        cookies=_admin_cookie(test_settings),
    )
    token_id = create_resp.json()["id"]
    response = await client.delete(
        f"/api/auth/tokens/{token_id}",
        cookies=_viewer_cookie(test_settings),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_routes_registered_in_real_app():
    """Smoke test: PAT routes are reachable through the real create_app."""
    from unittest.mock import patch
    from sqlalchemy import create_engine as _sa_create_engine
    from sqlalchemy.pool import StaticPool
    import backend.db.session as _db_session

    from backend.app import create_app
    from backend.config import Settings as RealSettings

    def _static_pool_engine(url, **kwargs):
        kwargs.setdefault("connect_args", {})["check_same_thread"] = False
        return _sa_create_engine(url, poolclass=StaticPool, **kwargs)

    settings = RealSettings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)
    with patch.object(_db_session, "create_engine", _static_pool_engine):
        real_app = create_app(settings=settings)
    Base.metadata.create_all(real_app.state.engine)

    with real_app.state.session_factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin User",
            is_admin=True,
        )
        session.add(admin)
        session.commit()

    transport = ASGITransport(app=real_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = make_test_token("W0NE", settings, is_admin=True, token_version=0)
        resp = await c.get("/api/auth/tokens", cookies={"access_token": token})
        assert resp.status_code == 200
        assert resp.json() == []
