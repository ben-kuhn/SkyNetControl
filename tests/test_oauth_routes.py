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
from backend.config import Settings
from backend.config_mgmt.oauth import OAuthProviderConfig, upsert_oauth_provider
from backend.config_mgmt.oauth_routes import oauth_router


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
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
    app.state.engine = engine
    app.state.settings = test_settings
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(oauth_router, prefix="/api/admin")
    return app


@pytest.fixture
async def admin_client(test_app, test_settings):
    factory = test_app.state.session_factory
    with factory() as db:
        admin = User(callsign="W0NE", oidc_subject="test:admin", name="Admin", role=UserRole.ADMIN)
        db.add(admin)
        db.commit()
        db.refresh(admin)
    token = create_access_token("W0NE", "admin", test_settings)
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, token


# 1
@pytest.mark.asyncio
async def test_list_providers_empty(admin_client):
    client, token = admin_client
    response = await client.get("/api/admin/oauth/providers", cookies={"access_token": token})
    assert response.status_code == 200
    assert response.json() == []


# 2
@pytest.mark.asyncio
async def test_upsert_and_list_provider(admin_client):
    client, token = admin_client
    response = await client.put(
        "/api/admin/oauth/providers/google",
        json={"name": "Google", "enabled": True, "client_id": "cid", "client_secret": "csec", "issuer_url": ""},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "google"
    assert body["client_secret"] == "***"  # redacted
    list_resp = await client.get("/api/admin/oauth/providers", cookies={"access_token": token})
    assert any(p["slug"] == "google" for p in list_resp.json())


# 3
@pytest.mark.asyncio
async def test_get_redacts_client_secret(admin_client):
    client, token = admin_client
    await client.put(
        "/api/admin/oauth/providers/github",
        json={"name": "GitHub", "enabled": True, "client_id": "gh", "client_secret": "REAL-SECRET", "issuer_url": ""},
        cookies={"access_token": token},
    )
    response = await client.get("/api/admin/oauth/providers/github", cookies={"access_token": token})
    assert response.status_code == 200
    assert response.json()["client_secret"] == "***"
    assert "REAL-SECRET" not in response.text


# 4
@pytest.mark.asyncio
async def test_upsert_blank_secret_preserves_existing(admin_client, test_app):
    client, token = admin_client
    await client.put(
        "/api/admin/oauth/providers/github",
        json={"name": "GitHub", "enabled": True, "client_id": "gh", "client_secret": "KEEP-ME", "issuer_url": ""},
        cookies={"access_token": token},
    )
    await client.put(
        "/api/admin/oauth/providers/github",
        json={"name": "GitHub", "enabled": True, "client_id": "gh-new", "client_secret": "", "issuer_url": ""},
        cookies={"access_token": token},
    )
    from backend.config_mgmt.oauth import get_oauth_provider
    factory = test_app.state.session_factory
    with factory() as db:
        p = get_oauth_provider(db, "github")
        assert p is not None
        assert p.client_secret == "KEEP-ME"
        assert p.client_id == "gh-new"


# 5
@pytest.mark.asyncio
async def test_upsert_dash_secret_clears(admin_client, test_app):
    client, token = admin_client
    await client.put(
        "/api/admin/oauth/providers/github",
        json={"name": "GitHub", "enabled": True, "client_id": "gh", "client_secret": "OLD", "issuer_url": ""},
        cookies={"access_token": token},
    )
    await client.put(
        "/api/admin/oauth/providers/github",
        json={"name": "GitHub", "enabled": True, "client_id": "gh", "client_secret": "-", "issuer_url": ""},
        cookies={"access_token": token},
    )
    from backend.config_mgmt.oauth import get_oauth_provider
    factory = test_app.state.session_factory
    with factory() as db:
        p = get_oauth_provider(db, "github")
        assert p is not None and p.client_secret == ""


# 6
@pytest.mark.asyncio
async def test_delete_provider(admin_client):
    client, token = admin_client
    await client.put(
        "/api/admin/oauth/providers/microsoft",
        json={"name": "Microsoft", "enabled": True, "client_id": "m", "client_secret": "s", "issuer_url": ""},
        cookies={"access_token": token},
    )
    response = await client.delete("/api/admin/oauth/providers/microsoft", cookies={"access_token": token})
    assert response.status_code == 204
    get_resp = await client.get("/api/admin/oauth/providers/microsoft", cookies={"access_token": token})
    assert get_resp.status_code == 404


# 7
@pytest.mark.asyncio
async def test_invalid_slug_rejected(admin_client):
    client, token = admin_client
    response = await client.put(
        "/api/admin/oauth/providers/bad-slug!",
        json={"name": "X", "enabled": True, "client_id": "c", "client_secret": "s", "issuer_url": ""},
        cookies={"access_token": token},
    )
    assert response.status_code == 400
    assert "slug" in response.text.lower()


# 8
@pytest.mark.asyncio
async def test_non_admin_forbidden(test_app):
    # Without an admin session, every CRUD call returns 401/403.
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for verb, path in [
            ("get", "/api/admin/oauth/providers"),
            ("get", "/api/admin/oauth/providers/google"),
            ("put", "/api/admin/oauth/providers/google"),
            ("delete", "/api/admin/oauth/providers/google"),
        ]:
            kwargs = {"json": {}} if verb == "put" else {}
            response = await getattr(client, verb)(path, **kwargs)
            assert response.status_code in (401, 403)


# 9
@pytest.mark.asyncio
async def test_slug_derive_endpoint(admin_client):
    client, token = admin_client
    response = await client.post(
        "/api/admin/oauth/providers/slug/derive?name=PocketID Auth",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    assert response.json() == {"slug": "pocketid-auth", "valid": True}


# 10
@pytest.mark.asyncio
async def test_slug_derive_rejects_reserved(admin_client):
    client, token = admin_client
    response = await client.post(
        "/api/admin/oauth/providers/slug/derive?name=Google",
        cookies={"access_token": token},
    )
    body = response.json()
    assert body["slug"] == "google"
    assert body["valid"] is False
    assert "reserved" in body["error"].lower()


# 11 — create requires a secret; preserve-existing semantics ("") is a no-op
# on first save and would silently produce a broken OAuth flow.
@pytest.mark.asyncio
async def test_upsert_empty_secret_on_create_rejected(admin_client):
    client, token = admin_client
    response = await client.put(
        "/api/admin/oauth/providers/github",
        json={"name": "GitHub", "enabled": True, "client_id": "gh", "client_secret": "", "issuer_url": ""},
        cookies={"access_token": token},
    )
    assert response.status_code == 400
    assert "client_secret" in response.text.lower()


# 12 — mutating routes emit audit log entries (parity with the flat-key config endpoint)
@pytest.mark.asyncio
async def test_upsert_and_delete_logged_to_audit(admin_client, test_app):
    client, token = admin_client
    from backend.audit.models import AuditLog
    await client.put(
        "/api/admin/oauth/providers/github",
        json={"name": "GitHub", "enabled": True, "client_id": "gh", "client_secret": "s", "issuer_url": ""},
        cookies={"access_token": token},
    )
    await client.delete("/api/admin/oauth/providers/github", cookies={"access_token": token})
    with test_app.state.session_factory() as db:
        actions = [r.action for r in db.query(AuditLog).order_by(AuditLog.id).all()]
    assert "oauth.provider.upserted" in actions
    assert "oauth.provider.deleted" in actions
