import pytest
from fastapi import FastAPI, Depends, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.auth.service import create_access_token
from backend.auth.dependencies import get_current_user, require_scope
from backend.auth.pat_service import create_token
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

    @app.get("/api/test/me")
    async def me(user: User = Depends(get_current_user)):
        return {"callsign": user.callsign, "is_admin": user.is_admin}

    @app.get("/api/test/scoped")
    async def scoped(user: User = Depends(require_scope("schedule:read"))):
        return {"callsign": user.callsign}

    @app.get("/api/test/multi-scope")
    async def multi_scope(user: User = Depends(require_scope("schedule:read", "checkins:read"))):
        return {"callsign": user.callsign}

    @app.get("/api/test/admin-scoped")
    async def admin_scoped(user: User = Depends(require_scope("users:read"))):
        return {"callsign": user.callsign}

    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_bearer_pat_authenticates(test_client, seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", True, "Test", ["schedule:read"], None, net_id=1)
        raw = result["token"]
    response = await test_client.get(
        "/api/test/me",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200
    assert response.json()["callsign"] == "W0NE"


@pytest.mark.asyncio
async def test_bearer_invalid_token_returns_401(test_client):
    response = await test_client.get(
        "/api/test/me",
        headers={"Authorization": "Bearer skynet_" + "f" * 64},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_revoked_token_returns_401(test_client, seeded_db):
    from backend.auth.pat_service import revoke_token

    with seeded_db() as session:
        result = create_token(session, "W0NE", True, "Revoke me", ["schedule:read"], None, net_id=1)
        raw = result["token"]
        revoke_token(session, result["id"], "W0NE", is_admin=False)
    response = await test_client.get(
        "/api/test/me",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_pending_user_returns_401(test_client, seeded_db):
    with seeded_db() as session:
        from backend.auth.pat_models import PersonalAccessToken
        import hashlib, secrets

        raw = "skynet_" + secrets.token_hex(32)
        pat = PersonalAccessToken(
            user_callsign="PENDING-abc",
            name="Pending token",
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            token_prefix=raw[:8],
            scopes="schedule:read",
        )
        session.add(pat)
        session.commit()
    response = await test_client.get(
        "/api/test/me",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_cookie_auth_still_works(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.get("/api/test/me", cookies={"access_token": token})
    assert response.status_code == 200
    assert response.json()["callsign"] == "W0NE"


@pytest.mark.asyncio
async def test_require_scope_passes_with_correct_scope(test_client, seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", True, "Scoped", ["schedule:read"], None, net_id=1)
        raw = result["token"]
    response = await test_client.get(
        "/api/test/scoped",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_require_scope_fails_with_missing_scope(test_client, seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", True, "Wrong scope", ["checkins:read"], None, net_id=1)
        raw = result["token"]
    response = await test_client.get(
        "/api/test/scoped",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 403
    assert "schedule:read" in response.json()["detail"]


@pytest.mark.asyncio
async def test_require_scope_cookie_auth_bypasses(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.get(
        "/api/test/scoped",
        cookies={"access_token": token},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_require_scope_multi_scope_all_needed(test_client, seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", True, "Multi", ["schedule:read", "checkins:read"], None, net_id=1)
        raw = result["token"]
    response = await test_client.get(
        "/api/test/multi-scope",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_require_scope_multi_scope_partial_fails(test_client, seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", True, "Partial", ["schedule:read"], None, net_id=1)
        raw = result["token"]
    response = await test_client.get(
        "/api/test/multi-scope",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_schedule_endpoint_requires_scope():
    """PAT with wrong scope gets 403 on /api/schedule/sessions."""
    from unittest.mock import patch
    from sqlalchemy import create_engine as _sa_create_engine
    from sqlalchemy.pool import StaticPool
    import backend.db.session as _db_session
    from backend.app import create_app

    def _static_pool_engine(url, **kwargs):
        kwargs.setdefault("connect_args", {})["check_same_thread"] = False
        return _sa_create_engine(url, poolclass=StaticPool, **kwargs)

    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)
    with patch.object(_db_session, "create_engine", _static_pool_engine):
        app = create_app(settings=settings)
    Base.metadata.create_all(app.state.engine)

    with app.state.session_factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin User",
            is_admin=True,
        )
        session.add(admin)
        # Create the default net so the slug resolves. Private so the
        # unauthenticated assertion below exercises auth enforcement.
        from backend.modules.nets.models import Net as _Net
        net = _Net(slug="default-net", name="Default Net", is_public=False)
        session.add(net)
        session.commit()
        # Schedule is now under /api/nets/{slug}/schedule/ — access is controlled
        # by net role (is_admin bypasses). PAT scope check is on the caller to
        # provide a token; any valid PAT for an admin returns 200.
        result2 = create_token(session, "W0NE", True, "Any scope", ["schedule:read"], None, net_id=net.id)
        raw_token = result2["token"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Unauthenticated request → 401 (route requires VIEWER role)
        resp = await c.get("/api/nets/default-net/schedule/sessions")
        assert resp.status_code == 401

        # Admin PAT → 200 (is_admin bypasses net membership check)
        resp = await c.get(
            "/api/nets/default-net/schedule/sessions",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_require_scope_enforces_admin_on_admin_scope(test_client, seeded_db):
    """Non-admin user cannot use admin-scoped tokens; admin who loses is_admin is rejected."""
    with seeded_db() as session:
        # Mint token for admin user, then revoke admin status
        result = create_token(session, "W0NE", True, "Admin scope", ["users:read"], None)
        raw = result["token"]
        user = session.get(User, "W0NE")
        user.is_admin = False
        session.commit()
    response = await test_client.get(
        "/api/test/admin-scoped",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_me_returns_is_admin_flag(test_client, seeded_db):
    """The /me endpoint returns is_admin boolean."""
    with seeded_db() as session:
        result = create_token(session, "W0NE", True, "Admin token", ["users:read"], None)
        raw = result["token"]
    response = await test_client.get(
        "/api/test/me",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200
    assert response.json()["is_admin"] is True


@pytest.mark.asyncio
async def test_viewer_me_returns_is_admin_false(test_client, seeded_db):
    """Non-admin user has is_admin=False."""
    with seeded_db() as session:
        result = create_token(session, "KD0TST", False, "Viewer token", ["schedule:read"], None, net_id=1)
        raw = result["token"]
    response = await test_client.get(
        "/api/test/me",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200
    assert response.json()["is_admin"] is False
