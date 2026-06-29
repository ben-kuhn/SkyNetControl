"""Tests for PUT /api/nets/{slug}/config/bulk."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.nets.models import Net, NetConfig, NetMembership, NetRole
from backend.modules.nets.routes import router as nets_router
from tests.conftest import make_test_token

_SETTINGS = Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)


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
        nc = User(callsign="W0NC", oidc_subject="oidc|nc", name="Net Control")
        outsider = User(callsign="W0OUT", oidc_subject="oidc|out", name="Outsider")
        net = Net(slug="weekly", name="Weekly Net")
        session.add_all([nc, outsider, net])
        session.flush()
        session.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        session.commit()
    return factory


@pytest.fixture
def app(db_factory):
    a = FastAPI()
    a.state.session_factory = db_factory
    a.state.settings = _SETTINGS
    a.include_router(nets_router)
    return a


def _nc_token():
    return make_test_token("W0NC", _SETTINGS, token_version=0)


def _outsider_token():
    return make_test_token("W0OUT", _SETTINGS, token_version=0)


@pytest.mark.asyncio
async def test_bulk_put_upserts_all_keys(app, db_factory):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put(
            "/api/nets/weekly/config/bulk",
            cookies={"access_token": _nc_token()},
            json={"values": {"winlink_enabled": "true", "default_net_control": "W0NE"}},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "count": 2}
    with db_factory() as s:
        net = s.query(Net).filter(Net.slug == "weekly").one()
        rows = {c.key: c.value for c in s.query(NetConfig).filter(NetConfig.net_id == net.id).all()}
    assert rows == {"winlink_enabled": "true", "default_net_control": "W0NE"}


@pytest.mark.asyncio
async def test_bulk_put_requires_net_control(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put(
            "/api/nets/weekly/config/bulk",
            cookies={"access_token": _outsider_token()},
            json={"values": {"k": "v"}},
        )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_bulk_put_unknown_net_returns_404(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put(
            "/api/nets/does-not-exist/config/bulk",
            cookies={"access_token": _nc_token()},
            json={"values": {"k": "v"}},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_bulk_put_empty_values_is_noop(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put(
            "/api/nets/weekly/config/bulk",
            cookies={"access_token": _nc_token()},
            json={"values": {}},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "count": 0}
