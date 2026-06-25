"""Tests for backend.modules.nets.service."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.modules.nets.service import (
    validate_slug,
    create_net,
    list_nets,
    update_net,
    delete_net,
    add_member,
    remove_member,
    list_memberships,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory()


def _seed_user(db, callsign="W0NE", *, is_admin=False):
    u = User(callsign=callsign, oidc_subject=f"sub|{callsign}", name=callsign, is_admin=is_admin)
    db.add(u)
    db.commit()
    return u


# ---------------------------------------------------------------------------
# Slug validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ok", ["a", "w0ne", "packet-net", "x9", "a-b-c"])
def test_valid_slugs(ok):
    validate_slug(ok)


@pytest.mark.parametrize("bad", ["", "-x", "x-", "a--b", "A", "x y", "x_y", "a" * 65])
def test_invalid_slugs(bad):
    with pytest.raises(ValueError):
        validate_slug(bad)


# ---------------------------------------------------------------------------
# create_net
# ---------------------------------------------------------------------------


def test_create_net_basic():
    db = _make_db()
    net = create_net(db, slug="w0ne", name="W0NE Weekly Net", creator_callsign="W0NE")
    assert net.id is not None
    assert net.slug == "w0ne"
    assert net.name == "W0NE Weekly Net"
    assert net.is_public is True


def test_create_net_duplicate_slug_raises():
    db = _make_db()
    create_net(db, slug="dup-net", name="Net One", creator_callsign="W0NE")
    with pytest.raises(ValueError, match="already exists"):
        create_net(db, slug="dup-net", name="Net Two", creator_callsign="W0NE")


def test_create_net_bad_slug_raises():
    db = _make_db()
    with pytest.raises(ValueError):
        create_net(db, slug="Bad_Slug!", name="Bad Net", creator_callsign="W0NE")


# ---------------------------------------------------------------------------
# list_nets
# ---------------------------------------------------------------------------


def test_list_nets_admin_sees_all():
    db = _make_db()
    admin = _seed_user(db, "W0ADM", is_admin=True)
    create_net(db, slug="net-a", name="Net A", creator_callsign="W0ADM")
    create_net(db, slug="net-b", name="Net B", creator_callsign="W0ADM")
    nets = list_nets(db, user=admin)
    assert len(nets) == 2


def test_list_nets_non_admin_sees_only_own():
    db = _make_db()
    admin = _seed_user(db, "W0ADM", is_admin=True)
    viewer = _seed_user(db, "KD0TST")
    net_a = create_net(db, slug="net-a", name="Net A", creator_callsign="W0ADM")
    net_b = create_net(db, slug="net-b", name="Net B", creator_callsign="W0ADM")
    add_member(db, net=net_a, callsign="KD0TST", role=NetRole.VIEWER)
    nets = list_nets(db, user=viewer)
    assert len(nets) == 1
    assert nets[0].slug == "net-a"


def test_list_nets_no_membership_returns_empty():
    db = _make_db()
    create_net(db, slug="net-x", name="Net X", creator_callsign="W0ADM")
    viewer = _seed_user(db, "KD0TST")
    nets = list_nets(db, user=viewer)
    assert nets == []


# ---------------------------------------------------------------------------
# update_net
# ---------------------------------------------------------------------------


def test_update_net_name():
    db = _make_db()
    net = create_net(db, slug="old-name", name="Old Name", creator_callsign="W0ADM")
    updated = update_net(db, net=net, name="New Name")
    assert updated.name == "New Name"
    assert updated.slug == "old-name"


def test_update_net_slug():
    db = _make_db()
    net = create_net(db, slug="old-slug", name="My Net", creator_callsign="W0ADM")
    updated = update_net(db, net=net, slug="new-slug")
    assert updated.slug == "new-slug"


def test_update_net_slug_conflict_raises():
    db = _make_db()
    create_net(db, slug="taken", name="Taken", creator_callsign="W0ADM")
    net = create_net(db, slug="other", name="Other", creator_callsign="W0ADM")
    with pytest.raises(ValueError, match="already exists"):
        update_net(db, net=net, slug="taken")


def test_update_net_is_public():
    db = _make_db()
    net = create_net(db, slug="pub-net", name="Public Net", creator_callsign="W0ADM")
    assert net.is_public is True
    updated = update_net(db, net=net, is_public=False)
    assert updated.is_public is False


# ---------------------------------------------------------------------------
# delete_net
# ---------------------------------------------------------------------------


def test_delete_net_removes_net_and_config():
    db = _make_db()
    _seed_user(db, "W0ADM", is_admin=True)
    net = create_net(db, slug="to-delete", name="Delete Me", creator_callsign="W0ADM")
    add_member(db, net=net, callsign="W0ADM", role=NetRole.NET_CONTROL)

    net_id = net.id
    delete_net(db, net=net)

    assert db.query(Net).filter(Net.id == net_id).one_or_none() is None
    assert db.query(NetMembership).filter(NetMembership.net_id == net_id).count() == 0


# ---------------------------------------------------------------------------
# add_member / remove_member
# ---------------------------------------------------------------------------


def test_add_member_creates_membership():
    db = _make_db()
    _seed_user(db, "KD0TST")
    net = create_net(db, slug="test-net", name="Test Net", creator_callsign="W0ADM")
    m = add_member(db, net=net, callsign="KD0TST", role=NetRole.VIEWER)
    assert m.user_callsign == "KD0TST"
    assert m.role == NetRole.VIEWER


def test_add_member_bumps_token_version():
    db = _make_db()
    u = _seed_user(db, "KD0TST")
    initial_tv = u.token_version
    net = create_net(db, slug="bump-net", name="Bump Net", creator_callsign="W0ADM")
    add_member(db, net=net, callsign="KD0TST", role=NetRole.VIEWER)
    db.refresh(u)
    assert u.token_version == initial_tv + 1


def test_add_member_updates_role_if_already_member():
    db = _make_db()
    _seed_user(db, "KD0TST")
    net = create_net(db, slug="role-net", name="Role Net", creator_callsign="W0ADM")
    add_member(db, net=net, callsign="KD0TST", role=NetRole.VIEWER)
    add_member(db, net=net, callsign="KD0TST", role=NetRole.NET_CONTROL)
    memberships = db.query(NetMembership).filter(
        NetMembership.net_id == net.id,
        NetMembership.user_callsign == "KD0TST",
    ).all()
    assert len(memberships) == 1
    assert memberships[0].role == NetRole.NET_CONTROL


def test_add_member_unknown_user_raises():
    db = _make_db()
    net = create_net(db, slug="err-net", name="Err Net", creator_callsign="W0ADM")
    with pytest.raises(ValueError, match="not found"):
        add_member(db, net=net, callsign="NOBODY", role=NetRole.VIEWER)


def test_remove_member_deletes_membership():
    db = _make_db()
    _seed_user(db, "KD0TST")
    net = create_net(db, slug="rm-net", name="RM Net", creator_callsign="W0ADM")
    add_member(db, net=net, callsign="KD0TST", role=NetRole.VIEWER)
    remove_member(db, net=net, callsign="KD0TST")
    assert db.query(NetMembership).filter(
        NetMembership.net_id == net.id,
        NetMembership.user_callsign == "KD0TST",
    ).one_or_none() is None


def test_remove_member_bumps_token_version():
    db = _make_db()
    u = _seed_user(db, "KD0TST")
    net = create_net(db, slug="rmtv-net", name="RMTV Net", creator_callsign="W0ADM")
    add_member(db, net=net, callsign="KD0TST", role=NetRole.VIEWER)
    db.refresh(u)
    tv_after_add = u.token_version
    remove_member(db, net=net, callsign="KD0TST")
    db.refresh(u)
    assert u.token_version == tv_after_add + 1


def test_remove_member_not_found_raises():
    db = _make_db()
    net = create_net(db, slug="nf-net", name="NF Net", creator_callsign="W0ADM")
    with pytest.raises(ValueError, match="not a member"):
        remove_member(db, net=net, callsign="NOBODY")


# ---------------------------------------------------------------------------
# list_memberships
# ---------------------------------------------------------------------------


def test_list_memberships_returns_dicts_with_name():
    db = _make_db()
    u = _seed_user(db, "KD0TST")
    u.name = "Test User"
    db.commit()
    net = create_net(db, slug="ls-net", name="LS Net", creator_callsign="W0ADM")
    add_member(db, net=net, callsign="KD0TST", role=NetRole.VIEWER)
    result = list_memberships(db, net=net)
    assert len(result) == 1
    assert result[0]["callsign"] == "KD0TST"
    assert result[0]["name"] == "Test User"
    assert result[0]["role"] == NetRole.VIEWER
