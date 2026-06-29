"""Tests for PUT /api/config/bulk."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.auth.secret_box import decrypt
from backend.config import Settings
from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.routes import config_router
from backend.db.base import Base
from tests.conftest import make_test_token


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)


@pytest.fixture
def db_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(User(callsign="W0ADM", oidc_subject="oidc|adm", name="Admin", is_admin=True))
        session.commit()
    return factory


@pytest.fixture
def app(db_factory, test_settings):
    a = FastAPI()
    a.state.session_factory = db_factory
    a.state.settings = test_settings
    a.include_router(config_router, prefix="/api/config")
    return a


def _auth_headers(test_settings):
    token = make_test_token("W0ADM", test_settings, is_admin=True, token_version=0)
    return {"Cookie": f"access_token={token}"}


@pytest.mark.asyncio
async def test_bulk_put_upserts_all_keys(app, db_factory, test_settings):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put(
            "/api/config/bulk",
            headers=_auth_headers(test_settings),
            json={"values": {"net_callsign_demo": "W0NE", "claude_model_demo": "opus"}},
        )
    assert r.status_code == 200, r.text
    with db_factory() as s:
        rows = {c.key: c.value for c in s.query(AppConfig).all()}
    assert rows["net_callsign_demo"] == "W0NE"
    assert rows["claude_model_demo"] == "opus"


@pytest.mark.asyncio
async def test_bulk_put_encrypts_sensitive_keys(app, db_factory, test_settings):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put(
            "/api/config/bulk",
            headers=_auth_headers(test_settings),
            json={"values": {"claude_api_key": "sk-secret", "public_field": "plain"}},
        )
    assert r.status_code == 200, r.text
    with db_factory() as s:
        rows = {c.key: c.value for c in s.query(AppConfig).all()}
    # public field stored plaintext
    assert rows["public_field"] == "plain"
    # sensitive field stored encrypted, decryptable back to original
    assert rows["claude_api_key"] != "sk-secret"
    assert decrypt(rows["claude_api_key"]) == "sk-secret"


@pytest.mark.asyncio
async def test_bulk_put_empty_values_returns_ok(app, test_settings):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put("/api/config/bulk", headers=_auth_headers(test_settings), json={"values": {}})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "count": 0}


@pytest.mark.asyncio
async def test_bulk_put_missing_values_field_returns_422(app, test_settings):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put("/api/config/bulk", headers=_auth_headers(test_settings), json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_put_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put("/api/config/bulk", json={"values": {"k": "v"}})
    assert r.status_code in (401, 403)
