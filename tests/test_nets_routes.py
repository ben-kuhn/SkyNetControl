"""Tests for backend.modules.nets.routes — /api/nets endpoints."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.config import Settings
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.modules.nets.routes import router as nets_router
from backend.modules.nets.service import add_member, create_net
from tests.conftest import make_test_token


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
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
    with factory() as db:
        admin = User(callsign="W0ADM", oidc_subject="sub|admin", name="Admin", is_admin=True)
        member = User(callsign="KD0TST", oidc_subject="sub|member", name="Test Member")
        outsider = User(callsign="W0OUT", oidc_subject="sub|outsider", name="Outsider")
        db.add_all([admin, member, outsider])
        db.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(nets_router)
    return app


@pytest.fixture
async def client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _admin_token(test_settings):
    return make_test_token("W0ADM", test_settings, is_admin=True)


def _member_token(test_settings, *, token_version=0):
    return make_test_token("KD0TST", test_settings, token_version=token_version)


def _outsider_token(test_settings):
    return make_test_token("W0OUT", test_settings)


# ---------------------------------------------------------------------------
# POST /api/nets — create net
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_net_as_admin(client, test_settings):
    token = _admin_token(test_settings)
    resp = await client.post(
        "/api/nets",
        json={"slug": "w0ne", "name": "W0NE Weekly"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "w0ne"
    assert data["name"] == "W0NE Weekly"
    assert data["is_public"] is True
    assert isinstance(data["id"], int)


@pytest.mark.asyncio
async def test_create_net_as_non_admin_returns_403(client, test_settings):
    token = _member_token(test_settings)
    resp = await client.post(
        "/api/nets",
        json={"slug": "unauth-net", "name": "Unauth"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_net_bad_slug_returns_400(client, test_settings):
    token = _admin_token(test_settings)
    resp = await client.post(
        "/api/nets",
        json={"slug": "Bad_Slug!", "name": "Bad"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_net_duplicate_slug_returns_400(client, test_settings, db_setup):
    with db_setup() as db:
        create_net(db, slug="dup-net", name="Dup", creator_callsign="W0ADM")
    token = _admin_token(test_settings)
    resp = await client.post(
        "/api/nets",
        json={"slug": "dup-net", "name": "Dup2"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/nets — list nets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_nets_admin_sees_all(client, test_settings, db_setup):
    with db_setup() as db:
        create_net(db, slug="net-a", name="Net A", creator_callsign="W0ADM")
        create_net(db, slug="net-b", name="Net B", creator_callsign="W0ADM")
    token = _admin_token(test_settings)
    resp = await client.get("/api/nets", cookies={"access_token": token})
    assert resp.status_code == 200
    slugs = {n["slug"] for n in resp.json()}
    assert {"net-a", "net-b"} == slugs


@pytest.mark.asyncio
async def test_list_nets_viewer_sees_only_own(client, test_settings, db_setup):
    with db_setup() as db:
        net_a = create_net(db, slug="vis-a", name="Visible", creator_callsign="W0ADM")
        create_net(db, slug="vis-b", name="Hidden", creator_callsign="W0ADM")
        add_member(db, net=net_a, callsign="KD0TST", role=NetRole.VIEWER)
    # token_version bumped by add_member; make token with tv=1
    token = make_test_token("KD0TST", test_settings, token_version=1)
    resp = await client.get("/api/nets", cookies={"access_token": token})
    assert resp.status_code == 200
    slugs = [n["slug"] for n in resp.json()]
    assert slugs == ["vis-a"]


@pytest.mark.asyncio
async def test_list_nets_outsider_sees_empty(client, test_settings):
    token = _outsider_token(test_settings)
    resp = await client.get("/api/nets", cookies={"access_token": token})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_nets_includes_role_for_member(client, test_settings, db_setup):
    """GET /api/nets includes the user's role per net."""
    with db_setup() as db:
        net_a = create_net(db, slug="role-test", name="Role Test", creator_callsign="W0ADM")
        add_member(db, net=net_a, callsign="KD0TST", role=NetRole.NET_CONTROL)
    token = make_test_token("KD0TST", test_settings, token_version=1)
    resp = await client.get("/api/nets", cookies={"access_token": token})
    assert resp.status_code == 200
    nets = resp.json()
    assert len(nets) == 1
    assert nets[0]["role"] == "net_control"


@pytest.mark.asyncio
async def test_list_nets_role_is_none_for_admin_without_membership(client, test_settings, db_setup):
    """Admin with no explicit membership sees role=null for nets they see implicitly via is_admin.

    create_net now auto-adds the creator as net_control, so to exercise the "admin
    sees a net but isn't a member" path we have to remove the membership after.
    """
    with db_setup() as db:
        net = create_net(db, slug="admin-only", name="Admin Only", creator_callsign="W0ADM")
        membership = db.get(NetMembership, ("W0ADM", net.id))
        db.delete(membership)
        db.commit()
    token = _admin_token(test_settings)
    resp = await client.get("/api/nets", cookies={"access_token": token})
    assert resp.status_code == 200
    nets = resp.json()
    assert len(nets) == 1
    assert nets[0]["role"] is None


# ---------------------------------------------------------------------------
# GET /api/nets/{net_slug}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_net_as_member(client, test_settings, db_setup):
    with db_setup() as db:
        net = create_net(db, slug="my-net", name="My Net", creator_callsign="W0ADM")
        add_member(db, net=net, callsign="KD0TST", role=NetRole.VIEWER)
    token = make_test_token("KD0TST", test_settings, token_version=1)
    resp = await client.get("/api/nets/my-net", cookies={"access_token": token})
    assert resp.status_code == 200
    assert resp.json()["slug"] == "my-net"


@pytest.mark.asyncio
async def test_get_net_nonexistent_returns_404(client, test_settings):
    token = _admin_token(test_settings)
    resp = await client.get("/api/nets/no-such-net", cookies={"access_token": token})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_net_as_outsider_returns_403(client, test_settings, db_setup):
    with db_setup() as db:
        net = create_net(db, slug="secret-net", name="Secret", creator_callsign="W0ADM")
        # Private net — public nets allow read access to outsiders.
        net.is_public = False
        db.commit()
    token = _outsider_token(test_settings)
    resp = await client.get("/api/nets/secret-net", cookies={"access_token": token})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PATCH /api/nets/{net_slug}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_net_name_as_net_control(client, test_settings, db_setup):
    with db_setup() as db:
        net = create_net(db, slug="editable", name="Old Name", creator_callsign="W0ADM")
        add_member(db, net=net, callsign="KD0TST", role=NetRole.NET_CONTROL)
    token = make_test_token("KD0TST", test_settings, token_version=1)
    resp = await client.patch(
        "/api/nets/editable",
        json={"name": "New Name"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_patch_slug_as_non_admin_net_control_returns_403(client, test_settings, db_setup):
    with db_setup() as db:
        net = create_net(db, slug="no-slug-change", name="Net", creator_callsign="W0ADM")
        add_member(db, net=net, callsign="KD0TST", role=NetRole.NET_CONTROL)
    token = make_test_token("KD0TST", test_settings, token_version=1)
    resp = await client.patch(
        "/api/nets/no-slug-change",
        json={"slug": "new-slug"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_patch_slug_as_admin(client, test_settings, db_setup):
    with db_setup() as db:
        create_net(db, slug="rename-me", name="Net", creator_callsign="W0ADM")
    token = _admin_token(test_settings)
    resp = await client.patch(
        "/api/nets/rename-me",
        json={"slug": "renamed"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert resp.json()["slug"] == "renamed"


@pytest.mark.asyncio
async def test_patch_net_as_viewer_returns_403(client, test_settings, db_setup):
    with db_setup() as db:
        net = create_net(db, slug="view-only", name="View", creator_callsign="W0ADM")
        add_member(db, net=net, callsign="KD0TST", role=NetRole.VIEWER)
    token = make_test_token("KD0TST", test_settings, token_version=1)
    resp = await client.patch(
        "/api/nets/view-only",
        json={"name": "Attempted Change"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/nets/{net_slug}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_net_as_admin(client, test_settings, db_setup):
    with db_setup() as db:
        create_net(db, slug="bye-net", name="Bye", creator_callsign="W0ADM")
    token = _admin_token(test_settings)
    resp = await client.delete("/api/nets/bye-net", cookies={"access_token": token})
    assert resp.status_code == 204
    # Confirm gone
    resp2 = await client.get("/api/nets/bye-net", cookies={"access_token": token})
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_delete_net_as_non_admin_returns_403(client, test_settings, db_setup):
    with db_setup() as db:
        net = create_net(db, slug="keep-me", name="Keep", creator_callsign="W0ADM")
        add_member(db, net=net, callsign="KD0TST", role=NetRole.NET_CONTROL)
    token = make_test_token("KD0TST", test_settings, token_version=1)
    resp = await client.delete("/api/nets/keep-me", cookies={"access_token": token})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /api/nets/{net_slug}/members/{callsign}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_member_as_admin(client, test_settings, db_setup):
    with db_setup() as db:
        create_net(db, slug="member-net", name="Member Net", creator_callsign="W0ADM")
    token = _admin_token(test_settings)
    resp = await client.put(
        "/api/nets/member-net/members/KD0TST",
        json={"role": "viewer"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["callsign"] == "KD0TST"
    assert data["role"] == "viewer"
    assert data["name"] == "Test Member"


@pytest.mark.asyncio
async def test_put_member_bumps_token_version(client, test_settings, db_setup):
    with db_setup() as db:
        create_net(db, slug="tv-net", name="TV Net", creator_callsign="W0ADM")
        user = db.get(User, "KD0TST")
        tv_before = user.token_version

    token = _admin_token(test_settings)
    await client.put(
        "/api/nets/tv-net/members/KD0TST",
        json={"role": "viewer"},
        cookies={"access_token": token},
    )
    with db_setup() as db:
        user = db.get(User, "KD0TST")
        assert user.token_version == tv_before + 1


@pytest.mark.asyncio
async def test_put_member_as_non_admin_returns_403(client, test_settings, db_setup):
    with db_setup() as db:
        net = create_net(db, slug="nonadmin-net", name="NonAdmin", creator_callsign="W0ADM")
        add_member(db, net=net, callsign="KD0TST", role=NetRole.NET_CONTROL)
    token = make_test_token("KD0TST", test_settings, token_version=1)
    resp = await client.put(
        "/api/nets/nonadmin-net/members/W0OUT",
        json={"role": "viewer"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_put_member_unknown_user_returns_404(client, test_settings, db_setup):
    with db_setup() as db:
        create_net(db, slug="unk-net", name="Unk", creator_callsign="W0ADM")
    token = _admin_token(test_settings)
    resp = await client.put(
        "/api/nets/unk-net/members/GHOST",
        json={"role": "viewer"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/nets/{net_slug}/members/{callsign}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_member_as_admin(client, test_settings, db_setup):
    with db_setup() as db:
        net = create_net(db, slug="rm-member", name="RM", creator_callsign="W0ADM")
        add_member(db, net=net, callsign="KD0TST", role=NetRole.VIEWER)
    token = _admin_token(test_settings)
    resp = await client.delete(
        "/api/nets/rm-member/members/KD0TST",
        cookies={"access_token": token},
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_member_not_found_returns_404(client, test_settings, db_setup):
    with db_setup() as db:
        create_net(db, slug="empty-net", name="Empty", creator_callsign="W0ADM")
    token = _admin_token(test_settings)
    resp = await client.delete(
        "/api/nets/empty-net/members/KD0TST",
        cookies={"access_token": token},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/nets/{net_slug}/members
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_members_as_viewer(client, test_settings, db_setup):
    with db_setup() as db:
        # W0ADM is auto-added as NET_CONTROL by create_net; KD0TST is added explicitly.
        net = create_net(db, slug="list-mem", name="List Mem", creator_callsign="W0ADM")
        add_member(db, net=net, callsign="KD0TST", role=NetRole.VIEWER)
    token = make_test_token("KD0TST", test_settings, token_version=1)
    resp = await client.get("/api/nets/list-mem/members", cookies={"access_token": token})
    assert resp.status_code == 200
    members = resp.json()
    by_callsign = {m["callsign"]: m["role"] for m in members}
    assert by_callsign == {"W0ADM": "net_control", "KD0TST": "viewer"}


# ---------------------------------------------------------------------------
# GET /api/nets/{net_slug}/config + PUT /api/nets/{net_slug}/config/{key}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_config_as_net_control(client, test_settings, db_setup):
    with db_setup() as db:
        net = create_net(db, slug="cfg-net", name="Cfg", creator_callsign="W0ADM")
        add_member(db, net=net, callsign="KD0TST", role=NetRole.NET_CONTROL)
    token = make_test_token("KD0TST", test_settings, token_version=1)
    resp = await client.get("/api/nets/cfg-net/config", cookies={"access_token": token})
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


@pytest.mark.asyncio
async def test_put_config_as_net_control(client, test_settings, db_setup):
    with db_setup() as db:
        net = create_net(db, slug="cfg2-net", name="Cfg2", creator_callsign="W0ADM")
        add_member(db, net=net, callsign="KD0TST", role=NetRole.NET_CONTROL)
    token = make_test_token("KD0TST", test_settings, token_version=1)
    resp = await client.put(
        "/api/nets/cfg2-net/config/net.name",
        json={"value": "W0NE"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_put_config_as_viewer_returns_403(client, test_settings, db_setup):
    with db_setup() as db:
        net = create_net(db, slug="viewcfg", name="ViewCfg", creator_callsign="W0ADM")
        add_member(db, net=net, callsign="KD0TST", role=NetRole.VIEWER)
    token = make_test_token("KD0TST", test_settings, token_version=1)
    resp = await client.put(
        "/api/nets/viewcfg/config/some.key",
        json={"value": "x"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 403
