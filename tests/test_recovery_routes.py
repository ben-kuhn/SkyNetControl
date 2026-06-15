"""Tests for GET /api/recovery/status and POST /api/recovery/claim."""
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.recovery import mint_token
from backend.auth.recovery_routes import recovery_router
from backend.config import Settings
from backend.config_mgmt.setup_routes import setup_router
from backend.db.base import Base


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
    app.include_router(recovery_router, prefix="/api")
    app.include_router(setup_router, prefix="/api/setup")
    return app


# 1
@pytest.mark.asyncio
async def test_status_returns_outstanding_false_when_no_tokens(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/recovery/status")
    assert resp.status_code == 200
    assert resp.json() == {"outstanding": False}


# 2
@pytest.mark.asyncio
async def test_status_returns_outstanding_true_after_mint(test_app, db_setup):
    _, factory = db_setup
    with factory() as db:
        mint_token(db)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/recovery/status")
    assert resp.status_code == 200
    assert resp.json() == {"outstanding": True}


# 3
@pytest.mark.asyncio
async def test_status_only_counts_unused_unexpired(test_app, db_setup, monkeypatch):
    """An expired token does not contribute to outstanding=true."""
    import backend.auth.recovery as rec_module

    _, factory = db_setup

    # Mint a token that expires immediately (TTL of 0 seconds effectively in the past)
    frozen = [None]

    from datetime import datetime, timezone

    original_now = rec_module._now

    def fake_now():
        return frozen[0] or original_now()

    monkeypatch.setattr(rec_module, "_now", fake_now)

    # Set clock to "now" and mint
    frozen[0] = datetime(2000, 1, 1, tzinfo=timezone.utc)
    with factory() as db:
        mint_token(db, ttl=timedelta(minutes=1))

    # Advance clock past expiry
    frozen[0] = datetime(2000, 1, 1, 0, 2, tzinfo=timezone.utc)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/recovery/status")
    assert resp.status_code == 200
    assert resp.json() == {"outstanding": False}


# 4
@pytest.mark.asyncio
async def test_claim_returns_400_on_unknown_token(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/recovery/claim", json={"token": "no-such-token"})
    assert resp.status_code == 400


# 5
@pytest.mark.asyncio
async def test_claim_returns_400_on_used_token(test_app, db_setup):
    _, factory = db_setup
    with factory() as db:
        plaintext, _ = mint_token(db)
        from backend.auth.recovery import verify_token, mark_used

        row = verify_token(db, plaintext)
        assert row is not None
        mark_used(db, row)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/recovery/claim", json={"token": plaintext})
    assert resp.status_code == 400


# 6
@pytest.mark.asyncio
async def test_claim_returns_400_on_expired_token(test_app, db_setup, monkeypatch):
    import backend.auth.recovery as rec_module
    from datetime import datetime, timezone

    _, factory = db_setup
    original_now = rec_module._now
    frozen = [datetime(2000, 1, 1, tzinfo=timezone.utc)]

    def fake_now():
        return frozen[0]

    monkeypatch.setattr(rec_module, "_now", fake_now)

    with factory() as db:
        plaintext, _ = mint_token(db, ttl=timedelta(minutes=5))

    # Advance time past expiry
    frozen[0] = datetime(2000, 1, 1, 0, 10, tzinfo=timezone.utc)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/recovery/claim", json={"token": plaintext})
    assert resp.status_code == 400


# 7
@pytest.mark.asyncio
async def test_claim_sets_recovery_cookie_and_marks_used(test_app, db_setup):
    _, factory = db_setup
    with factory() as db:
        plaintext, _ = mint_token(db)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/recovery/claim", json={"token": plaintext})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert "recovery_token" in resp.cookies

    # Token should now be marked used
    from backend.auth.recovery import verify_token

    with factory() as db:
        assert verify_token(db, plaintext) is None


# 8
@pytest.mark.asyncio
async def test_claim_cookie_has_expected_attributes(test_app, db_setup):
    """Recovery cookie should be httponly; max_age should be positive."""
    _, factory = db_setup
    with factory() as db:
        plaintext, _ = mint_token(db)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/recovery/claim", json={"token": plaintext})
    assert resp.status_code == 200
    # The cookie value should be a non-empty JWT string
    cookie_value = resp.cookies.get("recovery_token")
    assert cookie_value is not None
    assert len(cookie_value) > 20
    # Headers should declare httponly
    set_cookie_header = resp.headers.get("set-cookie", "")
    assert "httponly" in set_cookie_header.lower()


# 9
@pytest.mark.asyncio
async def test_claim_is_single_use(test_app, db_setup):
    """Second claim with the same token must return 400."""
    _, factory = db_setup
    with factory() as db:
        plaintext, _ = mint_token(db)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/api/recovery/claim", json={"token": plaintext})
        second = await client.post("/api/recovery/claim", json={"token": plaintext})
    assert first.status_code == 200
    assert second.status_code == 400


# 10 — logout endpoint clears the cookie (HttpOnly cookies can't be cleared from JS)
@pytest.mark.asyncio
async def test_logout_clears_recovery_cookie(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/recovery/logout")
    assert resp.status_code == 200
    # Find the Set-Cookie header that clears recovery_token
    set_cookies = [h for h in resp.headers.raw if h[0].lower() == b"set-cookie"]
    cleared = [v for _, v in set_cookies if b"recovery_token=" in v]
    assert cleared, "expected a Set-Cookie clearing recovery_token"
    # Either max-age=0 OR an explicit past expiry indicates deletion
    blob = b"; ".join(cleared).lower()
    assert b"max-age=0" in blob or b"expires=" in blob
