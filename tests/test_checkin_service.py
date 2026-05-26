import pytest
from datetime import date, datetime, time, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionType,
    SessionStatus,
)
from backend.modules.checkins.models import (
    RawMessage,
    MessageType,
    CheckIn,
    ParseStatus,
    TimingStatus,
    Member,
)
from backend.modules.checkins.service import (
    classify_timing,
    process_raw_message,
    scan_and_import_messages,
    get_checkins_for_session,
    create_manual_checkin,
    update_checkin,
    approve_session_checkins,
    is_new_member,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def season_and_session(db):
    season = NetSeason(
        name="Test Season",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 6, 30),
        day_of_week=3,
        time=time(18, 0),
    )
    db.add(season)
    db.flush()

    net_session = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 10),
        grace_period_hours=24.0,
        session_type=SessionType.REGULAR_CHECKIN,
    )
    db.add(net_session)
    db.commit()
    return season, net_session


def test_classify_timing_on_time(season_and_session):
    _, net_session = season_and_session
    received = datetime(2026, 4, 10, 18, 30, tzinfo=timezone.utc)
    assert classify_timing(net_session, received) == TimingStatus.ON_TIME


def test_classify_timing_early(season_and_session):
    _, net_session = season_and_session
    received = datetime(2026, 4, 9, 20, 0, tzinfo=timezone.utc)
    assert classify_timing(net_session, received) == TimingStatus.EARLY


def test_classify_timing_late(season_and_session):
    _, net_session = season_and_session
    received = datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc)
    assert classify_timing(net_session, received) == TimingStatus.LATE


def test_is_new_member(db):
    assert is_new_member(db, "W0NEW") is True

    member = Member(
        callsign="W0OLD",
        name="Old Timer",
        first_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_check_in_date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        total_check_ins=10,
    )
    db.add(member)
    db.commit()

    assert is_new_member(db, "W0OLD") is False


def test_process_raw_message_form(db, season_and_session):
    _, net_session = season_and_session
    raw = RawMessage(
        message_id="FORM001",
        from_address="W0ABC@winlink.org",
        received_at=datetime(2026, 4, 10, 18, 30, tzinfo=timezone.utc),
        subject="Check-in",
        body=(
            "Name: John Smith\n"
            "Callsign: W0ABC\n"
            "City: Denver\n"
            "County: Denver\n"
            "State: CO\n"
            "Mode: Winlink\n"
            "Comments: All good\n"
        ),
        message_type=MessageType.FORM,
    )
    db.add(raw)
    db.commit()

    checkin = process_raw_message(db, raw, net_session)
    assert checkin is not None
    assert checkin.callsign == "W0ABC"
    assert checkin.name == "John Smith"
    assert checkin.city == "Denver"
    assert checkin.mode == "Winlink"
    assert checkin.parse_status == ParseStatus.AUTO
    assert checkin.timing_status == TimingStatus.ON_TIME
    assert raw.parsed is True


def test_process_raw_message_low_confidence(db, season_and_session):
    _, net_session = season_and_session
    raw = RawMessage(
        message_id="LOW001",
        from_address="W0XYZ@winlink.org",
        received_at=datetime(2026, 4, 10, 18, 30, tzinfo=timezone.utc),
        subject="Hello",
        body="This is just a random email",
        message_type=MessageType.UNKNOWN,
    )
    db.add(raw)
    db.commit()

    checkin = process_raw_message(db, raw, net_session)
    assert checkin is not None
    assert checkin.parse_status == ParseStatus.MANUAL_REVIEW


def test_scan_and_import_deduplicates(db, season_and_session):
    _, net_session = season_and_session

    raw_messages = [
        {
            "message_id": "DUP_A",
            "from_address": "W0ABC@winlink.org",
            "to_address": "w0ne@winlink.org",
            "subject": "Check-in",
            "received_at": datetime(2026, 4, 10, 18, 0, tzinfo=timezone.utc),
            "body": "Name: John Smith\nCallsign: W0ABC\nCity: Denver\nState: CO\nMode: Winlink\n",
        },
        {
            "message_id": "DUP_B",
            "from_address": "W0ABC@winlink.org",
            "to_address": "w0ne@winlink.org",
            "subject": "Check-in corrected",
            "received_at": datetime(2026, 4, 10, 19, 0, tzinfo=timezone.utc),
            "body": "Name: John Smith\nCallsign: W0ABC\nCity: Aurora\nState: CO\nMode: Winlink\n",
        },
    ]
    checkins = scan_and_import_messages(db, raw_messages, net_session)

    assert len(checkins) == 1
    assert checkins[0].city == "Aurora"


def test_scan_and_import_skips_existing_message_ids(db, season_and_session):
    _, net_session = season_and_session

    existing = RawMessage(
        message_id="EXISTING",
        from_address="W0ABC@winlink.org",
        received_at=datetime(2026, 4, 10, 18, 0, tzinfo=timezone.utc),
        subject="Old",
        body="old",
        message_type=MessageType.PLAIN_TEXT,
        parsed=True,
    )
    db.add(existing)
    db.commit()

    raw_messages = [
        {
            "message_id": "EXISTING",
            "from_address": "W0ABC@winlink.org",
            "to_address": "w0ne@winlink.org",
            "subject": "Old",
            "received_at": datetime(2026, 4, 10, 18, 0, tzinfo=timezone.utc),
            "body": "old",
        },
    ]
    checkins = scan_and_import_messages(db, raw_messages, net_session)
    assert len(checkins) == 0


def test_get_checkins_for_session(db, season_and_session):
    _, net_session = season_and_session
    checkin = CheckIn(
        session_id=net_session.id,
        callsign="W0ABC",
        name="John",
        mode="Winlink",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
    )
    db.add(checkin)
    db.commit()

    results = get_checkins_for_session(db, net_session.id)
    assert len(results) == 1
    assert results[0].callsign == "W0ABC"


def test_create_manual_checkin(db, season_and_session):
    _, net_session = season_and_session
    checkin = create_manual_checkin(
        db,
        session_id=net_session.id,
        callsign="W0MAN",
        name="Manual Entry",
        mode="Voice Relay",
        city="Pueblo",
        state="CO",
    )
    assert checkin.id is not None
    assert checkin.parse_status == ParseStatus.MANUALLY_ENTERED
    assert checkin.callsign == "W0MAN"


def test_update_checkin(db, season_and_session):
    _, net_session = season_and_session
    checkin = CheckIn(
        session_id=net_session.id,
        callsign="W0ABC",
        name="John",
        mode="Winlink",
        parse_status=ParseStatus.MANUAL_REVIEW,
        timing_status=TimingStatus.ON_TIME,
    )
    db.add(checkin)
    db.commit()

    updated = update_checkin(db, checkin.id, name="John Smith", city="Denver", parse_status=ParseStatus.AUTO)
    assert updated is not None
    assert updated.name == "John Smith"
    assert updated.city == "Denver"
    assert updated.parse_status == ParseStatus.AUTO


def test_approve_session_checkins(db, season_and_session):
    _, net_session = season_and_session
    checkin = CheckIn(
        session_id=net_session.id,
        callsign="W0NEW",
        name="New Person",
        mode="Winlink",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=True,
    )
    db.add(checkin)
    db.commit()

    approve_session_checkins(db, net_session.id)

    db.refresh(net_session)
    assert net_session.status == SessionStatus.COMPLETED

    member = db.get(Member, "W0NEW")
    assert member is not None
    assert member.name == "New Person"
    assert member.total_check_ins == 1


def test_approve_updates_existing_member(db, season_and_session):
    _, net_session = season_and_session

    member = Member(
        callsign="W0OLD",
        name="Old Timer",
        first_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_check_in_date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        total_check_ins=10,
    )
    db.add(member)
    db.commit()

    checkin = CheckIn(
        session_id=net_session.id,
        callsign="W0OLD",
        name="Old Timer",
        mode="Winlink",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=False,
    )
    db.add(checkin)
    db.commit()

    approve_session_checkins(db, net_session.id)

    db.refresh(member)
    assert member.total_check_ins == 11


def test_get_checkins_by_callsign_returns_all_sessions_desc(db):
    """Returns (CheckIn, session_date) tuples for a callsign across sessions, newest first."""
    from datetime import date, time
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
    from backend.modules.checkins.models import CheckIn, ParseStatus, TimingStatus
    from backend.modules.checkins.service import get_checkins_by_callsign

    season = NetSeason(name="S", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                       day_of_week=3, time=time(18, 0))
    db.add(season); db.flush()
    s1 = NetSession(season_id=season.id, start_date=date(2026, 1, 15),
                    session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.COMPLETED)
    s2 = NetSession(season_id=season.id, start_date=date(2026, 2, 15),
                    session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.COMPLETED)
    db.add_all([s1, s2]); db.commit()

    db.add_all([
        CheckIn(session_id=s1.id, callsign="W0NE", name="A", mode="Voice",
                parse_status=ParseStatus.AUTO, timing_status=TimingStatus.ON_TIME, is_new_member=True),
        CheckIn(session_id=s2.id, callsign="W0NE", name="A", mode="Winlink",
                parse_status=ParseStatus.AUTO, timing_status=TimingStatus.ON_TIME, is_new_member=False),
        CheckIn(session_id=s1.id, callsign="K0XYZ", name="B", mode="Voice",
                parse_status=ParseStatus.AUTO, timing_status=TimingStatus.ON_TIME, is_new_member=True),
    ])
    db.commit()

    rows = get_checkins_by_callsign(db, "W0NE")
    # Newest first
    assert [r[0].mode for r in rows] == ["Winlink", "Voice"]
    assert [r[1] for r in rows] == [date(2026, 2, 15), date(2026, 1, 15)]

    # Case-insensitive
    rows_lower = get_checkins_by_callsign(db, "w0ne")
    assert [r[0].id for r in rows_lower] == [r[0].id for r in rows]

    assert get_checkins_by_callsign(db, "NOBODY") == []


def test_scan_creates_notification_when_checkins_imported(db, season_and_session):
    """After importing at least one check-in, the session's NCS gets a notification."""
    from datetime import datetime, timezone
    from backend.auth.models import User, UserRole
    from backend.modules.notifications.models import Notification, NotificationKind

    season, session = season_and_session
    session.net_control_callsign = "W0NE"
    db.add(User(callsign="W0NE", oidc_subject="x|w0ne", name="NCS", role=UserRole.NET_CONTROL))
    db.commit()

    raw_messages = [{
        "message_id": "MSG-NOTIFY-1",
        "from_address": "ka0xyz@winlink.org",
        "received_at": datetime.now(tz=timezone.utc),
        "subject": "Check-in",
        "body": "John Doe KA0XYZ Denver CO Winlink",
    }]

    from backend.modules.checkins.service import scan_and_import_messages
    imported = scan_and_import_messages(db, raw_messages, session)
    assert len(imported) >= 1

    rows = (
        db.query(Notification)
        .filter(
            Notification.recipient_callsign == "W0NE",
            Notification.kind == NotificationKind.CHECKINS_READY,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].link_url == f"/checkins?session={session.id}"


def test_scan_creates_no_notification_when_no_imports(db, season_and_session):
    """No new check-ins → no notification."""
    from backend.modules.notifications.models import Notification

    season, session = season_and_session
    from backend.modules.checkins.service import scan_and_import_messages
    result = scan_and_import_messages(db, [], session)
    assert result == []
    assert db.query(Notification).count() == 0
