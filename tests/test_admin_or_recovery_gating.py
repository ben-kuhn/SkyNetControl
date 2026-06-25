"""Tests for require_admin_or_recovery gating and /api/setup/status recovery_mode field."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.audit.models import AuditLog
from backend.auth.models import User
from backend.auth.recovery import mint_token
from backend.auth.recovery_routes import recovery_router
from backend.auth.routes import auth_router
from backend.config import Settings
from backend.config_mgmt.oauth_routes import oauth_router
from backend.config_mgmt.setup_routes import setup_router
from backend.db.base import Base
from tests.conftest import make_test_token


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
        app_base_url="http://testserver",
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
    app.state.engine = engine
    app.state.settings = test_settings
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(oauth_router, prefix="/api/admin")
    app.include_router(recovery_router, prefix="/api")
    app.include_router(setup_router, prefix="/api/setup")
    return app


@pytest.fixture
def admin_token(test_app, db_setup):
    """Create an admin user and return a JWT for them."""
    _, factory = db_setup
    with factory() as db:
        admin = User(callsign="W0NE", oidc_subject="test:admin", name="Admin", is_admin=True)
        db.add(admin)
        db.commit()
    return make_test_token("W0NE", test_app.state.settings, is_admin=True)


@pytest.fixture
async def valid_recovery_cookie(test_app, db_setup):
    """Mint a recovery token, claim it via POST /api/recovery/claim, yield cookie value."""
    _, factory = db_setup
    with factory() as db:
        plaintext, _ = mint_token(db)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/recovery/claim", json={"token": plaintext})
    assert resp.status_code == 200, f"claim failed: {resp.text}"
    yield resp.cookies["recovery_token"]


@pytest.fixture
def expired_recovery_cookie(test_app):
    """Build a recovery JWT with exp in the past directly via make_recovery_token + monkeypatched time."""
    from datetime import datetime, timedelta, timezone

    import backend.auth.recovery as rec_module

    settings = test_app.state.settings
    original_now = rec_module._now
    rec_module._now = lambda: datetime(2000, 1, 1, tzinfo=timezone.utc)
    try:
        # make_recovery_token uses _now() internally for the exp claim
        # but exp = _now() + 30min = 2000-01-01 00:30:00 UTC — already in the past now.
        cookie_value = rec_module.make_recovery_token("abcd1234", settings)
    finally:
        rec_module._now = original_now
    return cookie_value


# 1
@pytest.mark.asyncio
async def test_oauth_put_admits_admin_user(test_app, admin_token):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/admin/oauth/providers/github",
            json={"name": "GitHub", "enabled": True, "client_id": "cid", "client_secret": "csec", "issuer_url": ""},
            cookies={"access_token": admin_token},
        )
    assert resp.status_code == 200


# 2
@pytest.mark.asyncio
async def test_oauth_put_admits_recovery_cookie(test_app, valid_recovery_cookie):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/admin/oauth/providers/github",
            json={"name": "GitHub", "enabled": True, "client_id": "cid", "client_secret": "csec", "issuer_url": ""},
            cookies={"recovery_token": valid_recovery_cookie},
        )
    assert resp.status_code == 200


# 3
@pytest.mark.asyncio
async def test_oauth_put_rejects_unauthenticated(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/admin/oauth/providers/github",
            json={"name": "GitHub", "enabled": True, "client_id": "cid", "client_secret": "csec", "issuer_url": ""},
        )
    assert resp.status_code in (401, 403)


# 4
@pytest.mark.asyncio
async def test_oauth_put_rejects_expired_recovery_cookie(test_app, expired_recovery_cookie):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/admin/oauth/providers/github",
            json={"name": "GitHub", "enabled": True, "client_id": "cid", "client_secret": "csec", "issuer_url": ""},
            cookies={"recovery_token": expired_recovery_cookie},
        )
    assert resp.status_code in (401, 403)


# 5
@pytest.mark.asyncio
async def test_audit_actor_is_user_callsign_when_admin(test_app, admin_token, db_setup):
    import json

    _, factory = db_setup
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put(
            "/api/admin/oauth/providers/github",
            json={"name": "GitHub", "enabled": True, "client_id": "cid", "client_secret": "csec", "issuer_url": ""},
            cookies={"access_token": admin_token},
        )
    with factory() as db:
        entry = db.query(AuditLog).filter(AuditLog.action == "oauth.provider.upserted").first()
    assert entry is not None
    assert entry.actor_callsign == "W0NE"


# 6
@pytest.mark.asyncio
async def test_audit_actor_is_recovery_prefix_when_recovery(test_app, valid_recovery_cookie, db_setup):
    """When a recovery cookie is used, actor should be 'recovery:<prefix>'."""
    _, factory = db_setup
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put(
            "/api/admin/oauth/providers/github",
            json={"name": "GitHub", "enabled": True, "client_id": "cid", "client_secret": "csec", "issuer_url": ""},
            cookies={"recovery_token": valid_recovery_cookie},
        )
    with factory() as db:
        entry = db.query(AuditLog).filter(AuditLog.action == "oauth.provider.upserted").first()
    assert entry is not None
    assert entry.actor_callsign.startswith("recovery:")


# 7
@pytest.mark.asyncio
async def test_setup_status_recovery_mode_true_with_valid_cookie(test_app, valid_recovery_cookie):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/setup/status",
            cookies={"recovery_token": valid_recovery_cookie},
        )
    assert resp.status_code == 200
    assert resp.json()["recovery_mode"] is True


# 8
@pytest.mark.asyncio
async def test_setup_status_recovery_mode_false_without_cookie(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/setup/status")
    assert resp.status_code == 200
    assert resp.json()["recovery_mode"] is False
