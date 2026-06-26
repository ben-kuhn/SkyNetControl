import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.modules.notifications.models import Notification, NotificationKind



@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_notification_model_loads(db):
    from backend.auth.models import User
    user = User(callsign="W0NE", oidc_subject="x", name="X", is_admin=True)
    db.add(user)
    db.flush()

    from datetime import datetime, timezone

    n = Notification(
        recipient_callsign="W0NE",
        kind=NotificationKind.REMINDER_DRAFT,
        message="Test",
        link_url="/reminders",
        created_at=datetime.now(tz=timezone.utc),
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    assert n.id is not None
    assert n.read_at is None


def _seed_user(db, callsign="W0NE", is_admin=False):
    from backend.auth.models import User
    user = User(
        callsign=callsign,
        oidc_subject=f"sub|{callsign}",
        name=callsign,
        is_admin=is_admin,
    )
    db.add(user)
    db.flush()
    return user


def _ensure_net(db):
    from backend.modules.nets.models import Net
    net = db.query(Net).filter_by(slug="t").one_or_none()
    if net is None:
        net = Net(slug="t", name="Test Net")
        db.add(net)
        db.flush()
    return net


def _seed_session(db, ncs="W0NE"):
    from datetime import date, time
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus

    net = _ensure_net(db)
    season = NetSeason(
        net_id=net.id,
        name="S",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        day_of_week=3,
        time=time(18, 0),
    )
    db.add(season)
    db.flush()
    sess = NetSession(
        season_id=season.id,
        start_date=date(2026, 5, 28),
        session_type=SessionType.REGULAR_CHECKIN,
        status=SessionStatus.SCHEDULED,
        net_control_callsign=ncs,
    )
    db.add(sess)
    db.commit()
    return sess


def test_create_notification_inserts_row(db):
    from backend.modules.notifications.service import create_notification
    from backend.modules.notifications.models import NotificationKind

    _seed_user(db)
    sess = _seed_session(db)

    n = create_notification(
        db,
        "W0NE",
        NotificationKind.REMINDER_DRAFT,
        message="Reminder draft ready",
        link_url="/reminders",
        session_id=sess.id,
    )
    assert n.id is not None
    assert n.recipient_callsign == "W0NE"
    assert n.read_at is None


def test_create_notification_dedupes_unread(db):
    from backend.modules.notifications.service import create_notification
    from backend.modules.notifications.models import Notification, NotificationKind

    _seed_user(db)
    sess = _seed_session(db)

    a = create_notification(
        db,
        "W0NE",
        NotificationKind.REMINDER_DRAFT,
        message="A",
        link_url="/reminders",
        session_id=sess.id,
    )
    b = create_notification(
        db,
        "W0NE",
        NotificationKind.REMINDER_DRAFT,
        message="B",
        link_url="/reminders",
        session_id=sess.id,
    )
    assert a.id == b.id
    assert db.query(Notification).count() == 1


def test_create_notification_no_dedupe_after_read(db):
    from datetime import datetime, timezone
    from backend.modules.notifications.service import create_notification
    from backend.modules.notifications.models import Notification, NotificationKind

    _seed_user(db)
    sess = _seed_session(db)

    a = create_notification(
        db,
        "W0NE",
        NotificationKind.REMINDER_DRAFT,
        message="A",
        session_id=sess.id,
    )
    a.read_at = datetime.now(tz=timezone.utc)
    db.commit()

    b = create_notification(
        db,
        "W0NE",
        NotificationKind.REMINDER_DRAFT,
        message="B",
        session_id=sess.id,
    )
    assert b.id != a.id
    assert db.query(Notification).count() == 2


def test_create_notification_dedupe_off_always_inserts(db):
    from backend.modules.notifications.service import create_notification
    from backend.modules.notifications.models import Notification, NotificationKind

    _seed_user(db)
    sess = _seed_session(db)

    create_notification(
        db,
        "W0NE",
        NotificationKind.DELIVERY_FAILURE,
        message="X",
        session_id=sess.id,
        dedupe=False,
    )
    create_notification(
        db,
        "W0NE",
        NotificationKind.DELIVERY_FAILURE,
        message="Y",
        session_id=sess.id,
        dedupe=False,
    )
    assert db.query(Notification).count() == 2


def test_list_for_user_unread_only_by_default(db):
    from datetime import datetime, timezone
    from backend.modules.notifications.service import create_notification, list_for_user
    from backend.modules.notifications.models import NotificationKind

    _seed_user(db)
    sess = _seed_session(db)
    net = _ensure_net(db)

    a = create_notification(db, "W0NE", NotificationKind.REMINDER_DRAFT, message="A", session_id=sess.id)
    b = create_notification(db, "W0NE", NotificationKind.ROSTER_DRAFT, message="B", session_id=sess.id)
    a.read_at = datetime.now(tz=timezone.utc)
    db.commit()

    unread = list_for_user(db, "W0NE", net_id=net.id)
    assert [n.id for n in unread] == [b.id]

    all_ = list_for_user(db, "W0NE", net_id=net.id, include_read=True)
    assert {n.id for n in all_} == {a.id, b.id}


def test_mark_read_owned(db):
    from backend.modules.notifications.service import create_notification, mark_read
    from backend.modules.notifications.models import NotificationKind

    _seed_user(db)
    sess = _seed_session(db)
    net = _ensure_net(db)

    n = create_notification(db, "W0NE", NotificationKind.REMINDER_DRAFT, message="A", session_id=sess.id)
    updated = mark_read(db, n.id, "W0NE", net_id=net.id)
    assert updated is not None
    assert updated.read_at is not None


def test_mark_read_not_owned_returns_none(db):
    from backend.modules.notifications.service import create_notification, mark_read
    from backend.modules.notifications.models import NotificationKind

    _seed_user(db, callsign="W0NE")
    _seed_user(db, callsign="KD0OTH")
    sess = _seed_session(db)
    net = _ensure_net(db)

    n = create_notification(db, "W0NE", NotificationKind.REMINDER_DRAFT, message="A", session_id=sess.id)
    assert mark_read(db, n.id, "KD0OTH", net_id=net.id) is None


def test_mark_all_read_returns_count(db):
    from backend.modules.notifications.service import create_notification, mark_all_read
    from backend.modules.notifications.models import NotificationKind

    _seed_user(db)
    sess = _seed_session(db)
    net = _ensure_net(db)

    create_notification(db, "W0NE", NotificationKind.REMINDER_DRAFT, message="A", session_id=sess.id)
    create_notification(db, "W0NE", NotificationKind.ROSTER_DRAFT, message="B", session_id=sess.id)
    count = mark_all_read(db, "W0NE", net_id=net.id)
    assert count == 2


def test_mark_read_wrong_net_returns_none(db):
    """mark_read returns None when the notification belongs to a different net."""
    from backend.modules.notifications.service import create_notification, mark_read
    from backend.modules.notifications.models import NotificationKind
    from backend.modules.nets.models import Net
    from datetime import date, time
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus

    _seed_user(db)
    net_a = _ensure_net(db)
    net_b = Net(slug="b", name="Net B")
    db.add(net_b)
    db.flush()

    season_b = NetSeason(
        net_id=net_b.id,
        name="S",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        day_of_week=3,
        time=time(18, 0),
    )
    db.add(season_b)
    db.flush()
    sess_b = NetSession(
        season_id=season_b.id,
        start_date=date(2026, 5, 1),
        session_type=SessionType.REGULAR_CHECKIN,
        status=SessionStatus.SCHEDULED,
        net_control_callsign="W0NE",
    )
    db.add(sess_b)
    db.commit()

    n = create_notification(db, "W0NE", NotificationKind.REMINDER_DRAFT, message="B-net", session_id=sess_b.id)
    # Attempt to mark as read via net_a's id — should return None
    assert mark_read(db, n.id, "W0NE", net_id=net_a.id) is None


def test_mark_all_read_cross_net_isolation(db):
    """mark_all_read on net A leaves net B's notifications unread."""
    from backend.modules.notifications.service import create_notification, mark_all_read
    from backend.modules.notifications.models import NotificationKind, Notification
    from backend.modules.nets.models import Net
    from datetime import date, time
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus

    _seed_user(db)
    net_a = _ensure_net(db)
    sess_a = _seed_session(db)

    net_b = Net(slug="b2", name="Net B2")
    db.add(net_b)
    db.flush()
    season_b = NetSeason(
        net_id=net_b.id,
        name="S",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        day_of_week=3,
        time=time(18, 0),
    )
    db.add(season_b)
    db.flush()
    sess_b = NetSession(
        season_id=season_b.id,
        start_date=date(2026, 5, 1),
        session_type=SessionType.REGULAR_CHECKIN,
        status=SessionStatus.SCHEDULED,
        net_control_callsign="W0NE",
    )
    db.add(sess_b)
    db.commit()

    n_a = create_notification(db, "W0NE", NotificationKind.REMINDER_DRAFT, message="A", session_id=sess_a.id)
    n_b = create_notification(db, "W0NE", NotificationKind.ROSTER_DRAFT, message="B", session_id=sess_b.id)

    count = mark_all_read(db, "W0NE", net_id=net_a.id)
    assert count == 1  # only net A's notification

    db.refresh(n_a)
    db.refresh(n_b)
    assert n_a.read_at is not None
    assert n_b.read_at is None


def test_resolve_session_recipient_prefers_ncs(db):
    from backend.modules.notifications.service import resolve_session_recipient

    _seed_user(db, callsign="W0NE")
    _seed_user(db, callsign="W0ADM")
    sess = _seed_session(db, ncs="W0NE")
    assert resolve_session_recipient(db, sess) == "W0NE"


def test_resolve_session_recipient_falls_back_to_admin(db):
    from backend.modules.notifications.service import resolve_session_recipient
    from datetime import date, time
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus

    _seed_user(db, callsign="W0ADM", is_admin=True)

    net = _ensure_net(db)
    season = NetSeason(
        net_id=net.id,
        name="S",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        day_of_week=3,
        time=time(18, 0),
    )
    db.add(season)
    db.flush()
    sess = NetSession(
        season_id=season.id,
        start_date=date(2026, 5, 28),
        session_type=SessionType.REGULAR_CHECKIN,
        status=SessionStatus.SCHEDULED,
        net_control_callsign=None,
    )
    db.add(sess)
    db.commit()

    assert resolve_session_recipient(db, sess) == "W0ADM"


def test_resolve_session_recipient_returns_none_when_no_one(db):
    from backend.modules.notifications.service import resolve_session_recipient
    from datetime import date, time
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus

    net = _ensure_net(db)
    season = NetSeason(
        net_id=net.id,
        name="S",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        day_of_week=3,
        time=time(18, 0),
    )
    db.add(season)
    db.flush()
    sess = NetSession(
        season_id=season.id,
        start_date=date(2026, 5, 28),
        session_type=SessionType.REGULAR_CHECKIN,
        status=SessionStatus.SCHEDULED,
        net_control_callsign=None,
    )
    db.add(sess)
    db.commit()
    assert resolve_session_recipient(db, sess) is None
