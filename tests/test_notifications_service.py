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
    from backend.auth.models import User, UserRole
    user = User(callsign="W0NE", oidc_subject="x", name="X", role=UserRole.ADMIN)
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


def _seed_user(db, callsign="W0NE", role=None):
    from backend.auth.models import User, UserRole
    user = User(
        callsign=callsign,
        oidc_subject=f"sub|{callsign}",
        name=callsign,
        role=role or UserRole.NET_CONTROL,
    )
    db.add(user)
    db.flush()
    return user


def _seed_session(db, ncs="W0NE"):
    from datetime import date, time
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
    season = NetSeason(
        name="S", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        day_of_week=3, time=time(18, 0),
    )
    db.add(season)
    db.flush()
    sess = NetSession(
        season_id=season.id, start_date=date(2026, 5, 28),
        session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.SCHEDULED,
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
        db, "W0NE", NotificationKind.REMINDER_DRAFT,
        message="Reminder draft ready", link_url="/reminders", session_id=sess.id,
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
        db, "W0NE", NotificationKind.REMINDER_DRAFT,
        message="A", link_url="/reminders", session_id=sess.id,
    )
    b = create_notification(
        db, "W0NE", NotificationKind.REMINDER_DRAFT,
        message="B", link_url="/reminders", session_id=sess.id,
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
        db, "W0NE", NotificationKind.REMINDER_DRAFT,
        message="A", session_id=sess.id,
    )
    a.read_at = datetime.now(tz=timezone.utc)
    db.commit()

    b = create_notification(
        db, "W0NE", NotificationKind.REMINDER_DRAFT,
        message="B", session_id=sess.id,
    )
    assert b.id != a.id
    assert db.query(Notification).count() == 2


def test_create_notification_dedupe_off_always_inserts(db):
    from backend.modules.notifications.service import create_notification
    from backend.modules.notifications.models import Notification, NotificationKind
    _seed_user(db)
    sess = _seed_session(db)

    create_notification(
        db, "W0NE", NotificationKind.DELIVERY_FAILURE,
        message="X", session_id=sess.id, dedupe=False,
    )
    create_notification(
        db, "W0NE", NotificationKind.DELIVERY_FAILURE,
        message="Y", session_id=sess.id, dedupe=False,
    )
    assert db.query(Notification).count() == 2


def test_list_for_user_unread_only_by_default(db):
    from datetime import datetime, timezone
    from backend.modules.notifications.service import create_notification, list_for_user
    from backend.modules.notifications.models import NotificationKind
    _seed_user(db)
    sess = _seed_session(db)

    a = create_notification(db, "W0NE", NotificationKind.REMINDER_DRAFT, message="A", session_id=sess.id)
    b = create_notification(db, "W0NE", NotificationKind.ROSTER_DRAFT, message="B", session_id=sess.id)
    a.read_at = datetime.now(tz=timezone.utc)
    db.commit()

    unread = list_for_user(db, "W0NE")
    assert [n.id for n in unread] == [b.id]

    all_ = list_for_user(db, "W0NE", include_read=True)
    assert {n.id for n in all_} == {a.id, b.id}


def test_mark_read_owned(db):
    from backend.modules.notifications.service import create_notification, mark_read
    from backend.modules.notifications.models import NotificationKind
    _seed_user(db)
    sess = _seed_session(db)

    n = create_notification(db, "W0NE", NotificationKind.REMINDER_DRAFT, message="A", session_id=sess.id)
    updated = mark_read(db, n.id, "W0NE")
    assert updated is not None
    assert updated.read_at is not None


def test_mark_read_not_owned_returns_none(db):
    from backend.modules.notifications.service import create_notification, mark_read
    from backend.modules.notifications.models import NotificationKind
    _seed_user(db, callsign="W0NE")
    _seed_user(db, callsign="KD0OTH")
    sess = _seed_session(db)

    n = create_notification(db, "W0NE", NotificationKind.REMINDER_DRAFT, message="A", session_id=sess.id)
    assert mark_read(db, n.id, "KD0OTH") is None


def test_mark_all_read_returns_count(db):
    from backend.modules.notifications.service import create_notification, mark_all_read
    from backend.modules.notifications.models import NotificationKind
    _seed_user(db)
    sess = _seed_session(db)

    create_notification(db, "W0NE", NotificationKind.REMINDER_DRAFT, message="A", session_id=sess.id)
    create_notification(db, "W0NE", NotificationKind.ROSTER_DRAFT, message="B", session_id=sess.id)
    count = mark_all_read(db, "W0NE")
    assert count == 2


def test_resolve_session_recipient_prefers_ncs(db):
    from backend.modules.notifications.service import resolve_session_recipient
    _seed_user(db, callsign="W0NE")
    _seed_user(db, callsign="W0ADM")
    sess = _seed_session(db, ncs="W0NE")
    assert resolve_session_recipient(db, sess) == "W0NE"


def test_resolve_session_recipient_falls_back_to_admin(db):
    from backend.auth.models import UserRole
    from backend.modules.notifications.service import resolve_session_recipient
    _seed_user(db, callsign="W0ADM", role=UserRole.ADMIN)
    from datetime import date, time
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
    season = NetSeason(
        name="S", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        day_of_week=3, time=time(18, 0),
    )
    db.add(season); db.flush()
    sess = NetSession(
        season_id=season.id, start_date=date(2026, 5, 28),
        session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.SCHEDULED,
        net_control_callsign=None,
    )
    db.add(sess); db.commit()

    assert resolve_session_recipient(db, sess) == "W0ADM"


def test_resolve_session_recipient_returns_none_when_no_one(db):
    from backend.modules.notifications.service import resolve_session_recipient
    from datetime import date, time
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
    season = NetSeason(
        name="S", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        day_of_week=3, time=time(18, 0),
    )
    db.add(season); db.flush()
    sess = NetSession(
        season_id=season.id, start_date=date(2026, 5, 28),
        session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.SCHEDULED,
        net_control_callsign=None,
    )
    db.add(sess); db.commit()
    assert resolve_session_recipient(db, sess) is None
