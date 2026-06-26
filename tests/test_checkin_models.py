import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.checkins.models import (
    RawMessage,
    MessageType,
    CheckIn,
    ParseStatus,
    TimingStatus,
    Member,
)
import backend.modules.schedule.models  # noqa: F401 — FK to net_sessions


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def net_id(db):
    from tests.conftest import make_test_net
    return make_test_net(db).id


def test_create_raw_message(db: Session):
    msg = RawMessage(
        message_id="ABC123",
        from_address="W0ABC@winlink.org",
        received_at=datetime(2026, 4, 10, 18, 30, tzinfo=timezone.utc),
        subject="Check-in",
        body="John Smith W0ABC Denver Denver CO Winlink",
        message_type=MessageType.PLAIN_TEXT,
    )
    db.add(msg)
    db.commit()

    fetched = db.get(RawMessage, msg.id)
    assert fetched is not None
    assert fetched.message_id == "ABC123"
    assert fetched.message_type == MessageType.PLAIN_TEXT
    assert fetched.parsed is False


def test_raw_message_id_is_unique(db: Session):
    msg1 = RawMessage(
        message_id="DUP1",
        from_address="W0ABC@winlink.org",
        received_at=datetime(2026, 4, 10, 18, 0, tzinfo=timezone.utc),
        subject="First",
        body="body1",
        message_type=MessageType.PLAIN_TEXT,
    )
    msg2 = RawMessage(
        message_id="DUP1",
        from_address="W0XYZ@winlink.org",
        received_at=datetime(2026, 4, 10, 19, 0, tzinfo=timezone.utc),
        subject="Second",
        body="body2",
        message_type=MessageType.PLAIN_TEXT,
    )
    db.add(msg1)
    db.commit()
    db.add(msg2)
    with pytest.raises(Exception):
        db.commit()


def test_create_checkin(db: Session, net_id):
    from backend.modules.schedule.models import (
        NetSeason,
        NetSession,
        SessionType,
    )
    from datetime import date

    season = NetSeason(
        net_id=net_id,
        name="Test Season",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
    )
    db.add(season)
    db.flush()

    net_session = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 10),
        session_type=SessionType.REGULAR_CHECKIN,
    )
    db.add(net_session)
    db.flush()

    checkin = CheckIn(
        session_id=net_session.id,
        callsign="W0ABC",
        name="John Smith",
        city="Denver",
        county="Denver",
        state="CO",
        mode="Winlink",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=True,
    )
    db.add(checkin)
    db.commit()

    fetched = db.get(CheckIn, checkin.id)
    assert fetched is not None
    assert fetched.callsign == "W0ABC"
    assert fetched.timing_status == TimingStatus.ON_TIME
    assert fetched.is_new_member is True
    assert fetched.latitude is None


def test_checkin_with_raw_message(db: Session, net_id):
    from backend.modules.schedule.models import (
        NetSeason,
        NetSession,
        SessionType,
    )
    from datetime import date

    season = NetSeason(
        net_id=net_id,
        name="Test Season",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
    )
    db.add(season)
    db.flush()

    net_session = NetSession(
        season_id=season.id,
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 10),
        session_type=SessionType.REGULAR_CHECKIN,
    )
    db.add(net_session)
    db.flush()

    raw = RawMessage(
        message_id="MSG001",
        from_address="W0ABC@winlink.org",
        received_at=datetime(2026, 4, 10, 18, 0, tzinfo=timezone.utc),
        subject="Check-in",
        body="John Smith W0ABC Denver Denver CO Winlink",
        message_type=MessageType.PLAIN_TEXT,
    )
    db.add(raw)
    db.flush()

    checkin = CheckIn(
        session_id=net_session.id,
        raw_message_id=raw.id,
        callsign="W0ABC",
        name="John Smith",
        mode="Winlink",
        parse_status=ParseStatus.AUTO,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=False,
    )
    db.add(checkin)
    db.commit()

    fetched = db.get(CheckIn, checkin.id)
    assert fetched is not None
    assert fetched.raw_message_id == raw.id
    assert fetched.raw_message is not None
    assert fetched.raw_message.message_id == "MSG001"


def test_create_member(db: Session, net_id):
    member = Member(
        net_id=net_id,
        callsign="W0ABC",
        name="John Smith",
        first_check_in_date=datetime(2026, 1, 15, tzinfo=timezone.utc),
        last_check_in_date=datetime(2026, 4, 10, tzinfo=timezone.utc),
        total_check_ins=12,
    )
    db.add(member)
    db.commit()

    fetched = db.get(Member, (net_id, "W0ABC"))
    assert fetched is not None
    assert fetched.name == "John Smith"
    assert fetched.total_check_ins == 12


def test_member_composite_pk(db: Session, net_id):
    """Same callsign in different nets is allowed; same callsign in same net raises."""
    from tests.conftest import make_test_net
    net2 = make_test_net(db, slug="t2", name="Net 2")

    m1 = Member(
        net_id=net_id,
        callsign="W0ABC",
        name="John",
        first_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        total_check_ins=1,
    )
    # Same callsign, different net — should be allowed
    m2 = Member(
        net_id=net2.id,
        callsign="W0ABC",
        name="John",
        first_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        total_check_ins=1,
    )
    db.add_all([m1, m2])
    db.commit()

    # Same callsign, same net — must raise
    m3 = Member(
        net_id=net_id,
        callsign="W0ABC",
        name="Duplicate",
        first_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        total_check_ins=1,
    )
    db.add(m3)
    with pytest.raises(Exception):
        db.commit()


def test_winlink_form_message_type_persists(db: Session):
    """A RawMessage with the new WINLINK_FORM enum value roundtrips through SQLite."""
    msg = RawMessage(
        message_id="<wlf-1@example>",
        from_address="w0abc@winlink.org",
        received_at=datetime.now(tz=timezone.utc),
        subject="check-in",
        body="<RMS_Express_Form><variables/></RMS_Express_Form>",
        message_type=MessageType.WINLINK_FORM,
        parsed=False,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    assert msg.message_type == MessageType.WINLINK_FORM
    assert msg.message_type.value == "winlink_form"
