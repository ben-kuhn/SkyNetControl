"""End-to-end isolation + public-access guarantees for multi-net.

These tests bring up the *full* app (create_app + StaticPool sqlite) so the
checks run across the real router tree — covering /api/nets/{slug}/ schedule,
checkins, and PAT scoping in a single place. They are the belt-and-braces
companion to per-module cross-net assertions.

Conventions:
- Two nets: "alpha" (private), "beta" (public).
- W0NE is a global admin (bypasses net checks).
- KA0A is a member of "alpha" only.
- KB0B is a member of "beta" only.
- W0EXT is registered but a member of neither.
"""

from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine as _sa_create_engine
from sqlalchemy.pool import StaticPool

import backend.db.session as _db_session
from backend.app import create_app
from backend.auth.models import User
from backend.auth.pat_service import create_token as create_pat
from backend.config import Settings
from backend.db.base import Base
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionStatus,
    SessionType,
)
from tests.conftest import make_test_token


def _static_pool_engine(url, **kwargs):
    kwargs.setdefault("connect_args", {})["check_same_thread"] = False
    return _sa_create_engine(url, poolclass=StaticPool, **kwargs)


@pytest.fixture
def isolation_app():
    """Spin up create_app() backed by a single shared-memory sqlite database."""
    from unittest.mock import patch

    settings = Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )
    with patch.object(_db_session, "create_engine", _static_pool_engine):
        app = create_app(settings=settings)
    Base.metadata.create_all(app.state.engine)
    return app, settings


def _seed(app):
    """Seed two nets, four users, one season + session per net.

    Returns a dict with the IDs tests need.
    """
    with app.state.session_factory() as db:
        admin = User(callsign="W0NE", oidc_subject="auth0|admin", name="Admin", is_admin=True)
        ka = User(callsign="KA0A", oidc_subject="auth0|ka", name="Alpha Member")
        kb = User(callsign="KB0B", oidc_subject="auth0|kb", name="Beta Member")
        ext = User(callsign="W0EXT", oidc_subject="auth0|ext", name="Outsider")
        db.add_all([admin, ka, kb, ext])

        alpha = Net(slug="alpha", name="Alpha Net", is_public=False)
        beta = Net(slug="beta", name="Beta Net", is_public=True)
        db.add_all([alpha, beta])
        db.flush()

        db.add(NetMembership(user_callsign="KA0A", net_id=alpha.id, role=NetRole.VIEWER))
        db.add(NetMembership(user_callsign="KB0B", net_id=beta.id, role=NetRole.VIEWER))

        alpha_season = NetSeason(
            net_id=alpha.id,
            name="Alpha Spring",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 30),
            day_of_week=3,
            is_week_long=False,
            activity_cadence=2,
        )
        beta_season = NetSeason(
            net_id=beta.id,
            name="Beta Spring",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 30),
            day_of_week=3,
            is_week_long=False,
            activity_cadence=2,
        )
        db.add_all([alpha_season, beta_season])
        db.flush()

        alpha_session = NetSession(
            season_id=alpha_season.id,
            start_date=date(2026, 4, 8),
            end_date=date(2026, 4, 8),
            grace_period_hours=24.0,
            session_type=SessionType.REGULAR_CHECKIN,
            status=SessionStatus.SCHEDULED,
        )
        beta_session = NetSession(
            season_id=beta_season.id,
            start_date=date(2026, 4, 8),
            end_date=date(2026, 4, 8),
            grace_period_hours=24.0,
            session_type=SessionType.REGULAR_CHECKIN,
            status=SessionStatus.SCHEDULED,
        )
        db.add_all([alpha_session, beta_session])
        db.commit()

        return {
            "alpha_id": alpha.id,
            "beta_id": beta.id,
            "alpha_season_id": alpha_season.id,
            "beta_season_id": beta_season.id,
            "alpha_session_id": alpha_session.id,
            "beta_session_id": beta_session.id,
        }


@pytest.fixture
async def isolation_client(isolation_app):
    app, _ = isolation_app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Data isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seasons_isolated_between_nets(isolation_app, isolation_client):
    """Listing seasons on net A only returns net A's seasons, never net B's."""
    app, settings = isolation_app
    ids = _seed(app)
    admin_token = make_test_token("W0NE", settings, is_admin=True, token_version=0)

    resp_a = await isolation_client.get(
        "/api/nets/alpha/schedule/seasons", cookies={"access_token": admin_token}
    )
    assert resp_a.status_code == 200
    names = {s["name"] for s in resp_a.json()}
    assert names == {"Alpha Spring"}, f"Alpha season list leaked Beta: {names}"

    resp_b = await isolation_client.get(
        "/api/nets/beta/schedule/seasons", cookies={"access_token": admin_token}
    )
    assert resp_b.status_code == 200
    names = {s["name"] for s in resp_b.json()}
    assert names == {"Beta Spring"}

    # Cross-net session GET returns 404 (existence hidden) even for admins.
    resp_x = await isolation_client.get(
        f"/api/nets/alpha/schedule/sessions/{ids['beta_session_id']}",
        cookies={"access_token": admin_token},
    )
    assert resp_x.status_code == 404


@pytest.mark.asyncio
async def test_members_isolated_between_nets(isolation_app, isolation_client):
    """A check-in on net A creates a Member(net_id=A, ...); net B's directory
    must not surface that member."""
    app, settings = isolation_app
    _seed(app)
    admin_token = make_test_token("W0NE", settings, is_admin=True, token_version=0)

    # Seed a member row in alpha by direct DB insertion (avoids invoking the
    # checkin parser here — Member isolation is what we're testing).
    from backend.modules.checkins.models import Member

    with app.state.session_factory() as db:
        alpha = db.query(Net).filter_by(slug="alpha").one()
        db.add(
            Member(
                callsign="N0XYZ",
                net_id=alpha.id,
                name="Alpha Only",
                first_check_in_date=date(2026, 4, 1),
                last_check_in_date=date(2026, 4, 1),
                total_check_ins=1,
            )
        )
        db.commit()

    resp_a = await isolation_client.get(
        "/api/nets/alpha/checkins/members", cookies={"access_token": admin_token}
    )
    assert resp_a.status_code == 200
    callsigns_a = {m["callsign"] for m in resp_a.json()}
    assert "N0XYZ" in callsigns_a

    resp_b = await isolation_client.get(
        "/api/nets/beta/checkins/members", cookies={"access_token": admin_token}
    )
    assert resp_b.status_code == 200
    callsigns_b = {m["callsign"] for m in resp_b.json()}
    assert "N0XYZ" not in callsigns_b, "Member leaked from alpha into beta"


# ---------------------------------------------------------------------------
# PAT cross-net scoping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pat_scoped_to_net_a_cannot_access_net_b(isolation_app, isolation_client):
    """A PAT minted with net_id=A returns 403 on any /api/nets/B/* path."""
    app, _ = isolation_app
    ids = _seed(app)

    with app.state.session_factory() as db:
        # Mint a non-admin PAT for KA0A bound to alpha.
        result = create_pat(
            db,
            "KA0A",
            False,
            "Alpha-only token",
            ["schedule:read"],
            None,
            net_id=ids["alpha_id"],
        )
        raw = result["token"]

    # Same net → 200
    ok = await isolation_client.get(
        "/api/nets/alpha/schedule/sessions",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert ok.status_code == 200

    # Cross net → 403
    forbidden = await isolation_client.get(
        "/api/nets/beta/schedule/sessions",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert forbidden.status_code == 403


# ---------------------------------------------------------------------------
# Public / private gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_net_checkins_anonymous(isolation_app, isolation_client):
    """net.is_public=True: unauthenticated GET on the public-read allow-list
    returns 200 (today: schedule sessions list + per-session checkins)."""
    app, _ = isolation_app
    ids = _seed(app)

    # Anonymous request to a public net — schedule sessions list.
    resp_sessions = await isolation_client.get("/api/nets/beta/schedule/sessions")
    assert resp_sessions.status_code == 200, resp_sessions.text

    # Anonymous request to per-session checkins.
    resp_checkins = await isolation_client.get(
        f"/api/nets/beta/checkins/session/{ids['beta_session_id']}"
    )
    assert resp_checkins.status_code == 200

    # And the net resource itself.
    resp_net = await isolation_client.get("/api/nets/beta")
    assert resp_net.status_code == 200
    assert resp_net.json()["slug"] == "beta"


@pytest.mark.asyncio
async def test_private_net_checkins_anonymous(isolation_app, isolation_client):
    """net.is_public=False: anonymous GETs are rejected with 401."""
    app, _ = isolation_app
    ids = _seed(app)

    resp_sessions = await isolation_client.get("/api/nets/alpha/schedule/sessions")
    assert resp_sessions.status_code == 401

    resp_checkins = await isolation_client.get(
        f"/api/nets/alpha/checkins/session/{ids['alpha_session_id']}"
    )
    assert resp_checkins.status_code == 401

    resp_net = await isolation_client.get("/api/nets/alpha")
    assert resp_net.status_code == 401


# ---------------------------------------------------------------------------
# JWT invalidation on membership change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_membership_revoke_invalidates_jwt(isolation_app, isolation_client):
    """Removing a user's membership bumps token_version, which invalidates
    every JWT that user holds — even ones that would have authorized other
    nets they're still in. (token_version is global; the alternative would
    let a removed-from-A user keep a JWT that lets them keep using B until
    natural expiry. We accept the global bump as the simpler policy.)"""
    app, settings = isolation_app
    _seed(app)

    # Mint KA0A's JWT *before* their membership is removed.
    ka_token = make_test_token("KA0A", settings, token_version=0)

    # Pre-revocation: KA0A can list alpha seasons.
    resp = await isolation_client.get(
        "/api/nets/alpha/schedule/seasons", cookies={"access_token": ka_token}
    )
    assert resp.status_code == 200

    # Revoke membership via the service (which is what the API does).
    from backend.modules.nets.service import remove_member

    with app.state.session_factory() as db:
        alpha = db.query(Net).filter_by(slug="alpha").one()
        remove_member(db, net=alpha, callsign="KA0A")

    # Post-revocation: the *same* JWT is rejected (token_version bumped).
    resp = await isolation_client.get(
        "/api/nets/alpha/schedule/seasons", cookies={"access_token": ka_token}
    )
    assert resp.status_code == 401
