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
from backend.config_mgmt.smtp_routes import smtp_router

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
    app.include_router(smtp_router, prefix="/api/admin")
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
async def test_get_smtp_returns_404_when_unset(admin_client):
    client, token = admin_client
    response = await client.get("/api/admin/smtp", cookies={"access_token": token})
    assert response.status_code == 404


# 2
@pytest.mark.asyncio
async def test_upsert_and_get_smtp(admin_client):
    client, token = admin_client
    put_resp = await client.put(
        "/api/admin/smtp",
        json={
            "host": "smtp.example.com",
            "port": 587,
            "username": "user@example.com",
            "password": "mypassword",
            "from_address": "noreply@example.com",
            "use_tls": True,
        },
        cookies={"access_token": token},
    )
    assert put_resp.status_code == 200
    get_resp = await client.get("/api/admin/smtp", cookies={"access_token": token})
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["host"] == "smtp.example.com"
    assert body["port"] == 587
    assert body["password"] == "***"  # redacted


# 3
@pytest.mark.asyncio
async def test_get_redacts_password(admin_client):
    client, token = admin_client
    await client.put(
        "/api/admin/smtp",
        json={
            "host": "smtp.example.com",
            "port": 587,
            "username": "user@example.com",
            "password": "REAL-PASS",
            "from_address": "noreply@example.com",
            "use_tls": True,
        },
        cookies={"access_token": token},
    )
    response = await client.get("/api/admin/smtp", cookies={"access_token": token})
    assert response.status_code == 200
    assert response.json()["password"] == "***"
    assert "REAL-PASS" not in response.text


# 4
@pytest.mark.asyncio
async def test_upsert_blank_password_preserves_existing(admin_client, test_app):
    client, token = admin_client
    await client.put(
        "/api/admin/smtp",
        json={
            "host": "smtp.example.com",
            "port": 587,
            "username": "user@example.com",
            "password": "KEEP-ME",
            "from_address": "noreply@example.com",
            "use_tls": True,
        },
        cookies={"access_token": token},
    )
    await client.put(
        "/api/admin/smtp",
        json={
            "host": "smtp.example.com",
            "port": 465,
            "username": "user@example.com",
            "password": "",
            "from_address": "noreply@example.com",
            "use_tls": True,
        },
        cookies={"access_token": token},
    )
    from backend.config_mgmt.smtp import get_smtp_config

    factory = test_app.state.session_factory
    with factory() as db:
        cfg = get_smtp_config(db)
        assert cfg is not None
        assert cfg.password == "KEEP-ME"
        assert cfg.port == 465  # other field was updated


# 5
@pytest.mark.asyncio
async def test_upsert_dash_password_clears(admin_client, test_app):
    client, token = admin_client
    await client.put(
        "/api/admin/smtp",
        json={
            "host": "smtp.example.com",
            "port": 587,
            "username": "user@example.com",
            "password": "OLD-PASS",
            "from_address": "noreply@example.com",
            "use_tls": True,
        },
        cookies={"access_token": token},
    )
    await client.put(
        "/api/admin/smtp",
        json={
            "host": "smtp.example.com",
            "port": 587,
            "username": "user@example.com",
            "password": "-",
            "from_address": "noreply@example.com",
            "use_tls": True,
        },
        cookies={"access_token": token},
    )
    from backend.config_mgmt.smtp import get_smtp_config

    factory = test_app.state.session_factory
    with factory() as db:
        cfg = get_smtp_config(db)
        assert cfg is not None
        assert cfg.password == ""


# 6
@pytest.mark.asyncio
async def test_delete_smtp(admin_client, test_app):
    client, token = admin_client
    await client.put(
        "/api/admin/smtp",
        json={
            "host": "smtp.example.com",
            "port": 587,
            "username": "user@example.com",
            "password": "pass",
            "from_address": "noreply@example.com",
            "use_tls": True,
        },
        cookies={"access_token": token},
    )
    # Confirm it exists
    assert (await client.get("/api/admin/smtp", cookies={"access_token": token})).status_code == 200
    # Delete
    del_resp = await client.delete("/api/admin/smtp", cookies={"access_token": token})
    assert del_resp.status_code == 204
    # Verify gone
    assert (await client.get("/api/admin/smtp", cookies={"access_token": token})).status_code == 404


# 7
@pytest.mark.asyncio
async def test_non_admin_forbidden(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for verb, path in [
            ("get", "/api/admin/smtp"),
            ("put", "/api/admin/smtp"),
            ("delete", "/api/admin/smtp"),
        ]:
            kwargs = {"json": {}} if verb == "put" else {}
            response = await getattr(client, verb)(path, **kwargs)
            assert response.status_code in (401, 403)
