"""Tests for the require_net_role dependency and NetContext dataclass."""

import pytest
from fastapi import FastAPI, Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.auth.dependencies import require_net_role, NetContext
from backend.modules.nets.models import Net, NetMembership, NetRole
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
def db_factory(db_engine):
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture
def seeded_db(db_factory):
    """Seed DB: two nets, three users with varying membership roles."""
    with db_factory() as session:
        admin = User(callsign="W0ADM", oidc_subject="sub|adm", name="Admin", is_admin=True)
        nc = User(callsign="W0NC", oidc_subject="sub|nc", name="Net Control")
        viewer = User(callsign="W0VW", oidc_subject="sub|vw", name="Viewer")
        outsider = User(callsign="W0OUT", oidc_subject="sub|out", name="Outsider")
        session.add_all([admin, nc, viewer, outsider])

        net_alpha = Net(slug="alpha", name="Alpha Net")
        net_beta = Net(slug="beta", name="Beta Net")
        session.add_all([net_alpha, net_beta])
        session.flush()

        session.add(NetMembership(user_callsign="W0NC", net_id=net_alpha.id, role=NetRole.NET_CONTROL))
        session.add(NetMembership(user_callsign="W0VW", net_id=net_alpha.id, role=NetRole.VIEWER))
        # nc also has viewer role on beta
        session.add(NetMembership(user_callsign="W0NC", net_id=net_beta.id, role=NetRole.VIEWER))
        session.commit()
    return db_factory


@pytest.fixture
def test_app(test_settings, seeded_db):
    app = FastAPI()
    app.state.session_factory = seeded_db
    app.state.settings = test_settings

    require_viewer = require_net_role(NetRole.VIEWER)
    require_nc = require_net_role(NetRole.NET_CONTROL)

    @app.get("/nets/{net_slug}/viewer-gate")
    async def viewer_gate(ctx: NetContext = Depends(require_viewer)):
        return {
            "callsign": ctx.user.callsign,
            "net_slug": ctx.net.slug,
            "role": ctx.role.value if ctx.role else None,
        }

    @app.get("/nets/{net_slug}/nc-gate")
    async def nc_gate(ctx: NetContext = Depends(require_nc)):
        return {
            "callsign": ctx.user.callsign,
            "net_slug": ctx.net.slug,
            "role": ctx.role.value if ctx.role else None,
        }

    return app


@pytest.fixture
async def client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_viewer_can_access_viewer_gate(client, test_settings):
    token = make_test_token("W0VW", test_settings, token_version=0)
    resp = await client.get("/nets/alpha/viewer-gate", cookies={"access_token": token})
    assert resp.status_code == 200
    data = resp.json()
    assert data["callsign"] == "W0VW"
    assert data["net_slug"] == "alpha"
    assert data["role"] == "viewer"


@pytest.mark.asyncio
async def test_net_control_can_access_viewer_gate(client, test_settings):
    token = make_test_token("W0NC", test_settings, token_version=0)
    resp = await client.get("/nets/alpha/viewer-gate", cookies={"access_token": token})
    assert resp.status_code == 200
    assert resp.json()["role"] == "net_control"


@pytest.mark.asyncio
async def test_net_control_can_access_nc_gate(client, test_settings):
    token = make_test_token("W0NC", test_settings, token_version=0)
    resp = await client.get("/nets/alpha/nc-gate", cookies={"access_token": token})
    assert resp.status_code == 200
    assert resp.json()["role"] == "net_control"


@pytest.mark.asyncio
async def test_viewer_cannot_access_nc_gate(client, test_settings):
    """VIEWER role is below NET_CONTROL minimum — must get 403."""
    token = make_test_token("W0VW", test_settings, token_version=0)
    resp = await client.get("/nets/alpha/nc-gate", cookies={"access_token": token})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_bypasses_membership_check(client, test_settings):
    """Admin users access all nets regardless of membership rows."""
    token = make_test_token("W0ADM", test_settings, is_admin=True, token_version=0)
    # Admin has no NetMembership rows for alpha or beta
    resp = await client.get("/nets/alpha/nc-gate", cookies={"access_token": token})
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] is None  # NetContext.role is None for admin bypass


@pytest.mark.asyncio
async def test_outsider_denied_viewer_gate(client, test_settings):
    """User with no membership for the net is denied at viewer level."""
    token = make_test_token("W0OUT", test_settings, token_version=0)
    resp = await client.get("/nets/alpha/viewer-gate", cookies={"access_token": token})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_of_alpha_denied_on_beta(client, test_settings):
    """Membership on one net doesn't grant access to another."""
    token = make_test_token("W0VW", test_settings, token_version=0)
    resp = await client.get("/nets/beta/viewer-gate", cookies={"access_token": token})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_nc_of_alpha_with_viewer_on_beta_denied_nc_gate(client, test_settings):
    """User with VIEWER role on beta cannot pass NET_CONTROL gate for beta."""
    token = make_test_token("W0NC", test_settings, token_version=0)
    resp = await client.get("/nets/beta/nc-gate", cookies={"access_token": token})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unknown_net_returns_404(client, test_settings):
    """Request for a net that doesn't exist gets 404, not 403."""
    token = make_test_token("W0NC", test_settings, token_version=0)
    resp = await client.get("/nets/nonexistent/viewer-gate", cookies={"access_token": token})
    assert resp.status_code == 404
