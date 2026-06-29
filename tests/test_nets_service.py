"""Tests for backend.modules.nets.service."""
from unittest.mock import patch

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


def test_create_net_calls_seed_default_net_content():
    db = _make_db()
    with patch("backend.modules.nets.service.net_seeds.seed_default_net_content") as mock_seed:
        net = create_net(db, slug="seed-net", name="Seed Net", creator_callsign="W0NE")
        mock_seed.assert_called_once_with(db, net.id)


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


def test_delete_net_cascades_per_net_rows():
    """delete_net must drop every per-net row, not just config + memberships.
    Postgres would otherwise reject the deletion on FK violation; SQLite would
    silently orphan rows (FK enforcement is off by default)."""
    from datetime import date, datetime, timezone

    from backend.modules.activities.models import (
        Activity,
        ActivityTag,
        ActivityTagAssignment,
        ChatSession,
    )
    from backend.modules.checkins.models import CheckIn, Member, ParseStatus, TimingStatus
    from backend.modules.reminders.models import ReminderLog, ReminderTemplate
    from backend.modules.roster.models import RosterLog, RosterTemplate
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType

    db = _make_db()
    _seed_user(db, "W0ADM", is_admin=True)
    net = create_net(db, slug="full", name="Full Net", creator_callsign="W0ADM")
    net_id = net.id
    now = datetime.now(tz=timezone.utc)

    season = NetSeason(
        net_id=net_id, name="S1",
        start_date=date(2026, 1, 1), end_date=date(2026, 3, 31),
        day_of_week=3,
    )
    db.add(season); db.flush()
    net_session = NetSession(
        season_id=season.id,
        start_date=date(2026, 1, 7), end_date=date(2026, 1, 7),
        grace_period_hours=24.0, session_type=SessionType.REGULAR_CHECKIN,
    )
    db.add(net_session); db.flush()

    db.add(CheckIn(
        session_id=net_session.id, callsign="K0X", name="X",
        mode="voice", parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME, is_new_member=False,
    ))
    db.add(RosterLog(
        session_id=net_session.id, content_subject="", content_header="",
        content_welcome="", content_comments="", content_footer="", drafted_at=now,
    ))
    db.add(ReminderLog(
        session_id=net_session.id, content_subject="", content_body="", drafted_at=now,
    ))
    db.add(Member(
        callsign="K0X", net_id=net_id, name="X",
        first_check_in_date=now, last_check_in_date=now, total_check_ins=1,
    ))
    activity = Activity(net_id=net_id, title="Trivia", description="x", instructions="x")
    db.add(activity); db.flush()
    tag = ActivityTag(net_id=net_id, name="trivia")
    db.add(tag); db.flush()
    db.add(ActivityTagAssignment(activity_id=activity.id, tag_id=tag.id))
    chat = ChatSession(activity_id=activity.id)
    db.add(chat); db.flush()
    db.add(RosterTemplate(
        net_id=net_id, name="custom-roster", subject_template="",
        header_template="", welcome_template="", comments_template="", footer_template="",
    ))
    from backend.modules.reminders.models import TemplateType
    db.add(ReminderTemplate(
        net_id=net_id, name="custom-reminder",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="", body_template="",
    ))
    db.commit()
    chat_id = chat.id
    season_id = season.id
    session_id = net_session.id

    delete_net(db, net=net)
    db.expire_all()

    assert db.query(NetSeason).filter_by(net_id=net_id).count() == 0
    assert db.query(NetSession).filter_by(season_id=season_id).count() == 0
    assert db.query(CheckIn).filter_by(session_id=session_id).count() == 0
    assert db.query(Member).filter_by(net_id=net_id).count() == 0
    assert db.query(Activity).filter_by(net_id=net_id).count() == 0
    assert db.query(ActivityTag).filter_by(net_id=net_id).count() == 0
    assert db.query(RosterTemplate).filter_by(net_id=net_id).count() == 0
    assert db.query(ReminderTemplate).filter_by(net_id=net_id).count() == 0
    assert db.query(RosterLog).filter_by(session_id=session_id).count() == 0
    assert db.query(ReminderLog).filter_by(session_id=session_id).count() == 0
    assert db.query(Net).filter_by(id=net_id).one_or_none() is None
    # ChatSession survives but loses its activity FK (chats outlive activities).
    surviving_chat = db.get(ChatSession, chat_id)
    assert surviving_chat is not None and surviving_chat.activity_id is None


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
