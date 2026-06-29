import pytest
from datetime import date, time
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.nets.models import Net
from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def net(db: Session) -> Net:
    n = Net(slug="t", name="Test Net")
    db.add(n)
    db.commit()
    return n


def test_create_season(db: Session, net: Net):
    season = NetSeason(
        net_id=net.id,
        name="Fall/Winter 2026",
        start_date=date(2026, 9, 7),
        end_date=date(2027, 5, 26),
        day_of_week=3,  # Thursday
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()

    fetched = db.get(NetSeason, season.id)
    assert fetched is not None
    assert fetched.name == "Fall/Winter 2026"
    assert fetched.day_of_week == 3
    assert fetched.activity_cadence == 2
    assert fetched.net_id == net.id


def test_create_session(db: Session, net: Net):
    season = NetSeason(
        net_id=net.id,
        name="Test Season",
        start_date=date(2026, 9, 7),
        end_date=date(2027, 5, 26),
        day_of_week=3,
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()

    session_obj = NetSession(
        season_id=season.id,
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 11),
        grace_period_hours=24,
        session_type=SessionType.REGULAR_CHECKIN,
        status=SessionStatus.SCHEDULED,
        net_control_callsign="W0NE",
    )
    db.add(session_obj)
    db.commit()

    fetched = db.get(NetSession, session_obj.id)
    assert fetched is not None
    assert fetched.session_type == SessionType.REGULAR_CHECKIN
    assert fetched.status == SessionStatus.SCHEDULED
    assert fetched.net_control_callsign == "W0NE"
    assert fetched.grace_period_hours == 24
    assert fetched.activity_id is None


def test_session_belongs_to_season(db: Session, net: Net):
    season = NetSeason(
        net_id=net.id,
        name="Test",
        start_date=date(2026, 9, 7),
        end_date=date(2027, 5, 26),
        day_of_week=3,
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()

    s1 = NetSession(
        season_id=season.id,
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 11),
        grace_period_hours=24,
        session_type=SessionType.REGULAR_CHECKIN,
        status=SessionStatus.SCHEDULED,
        net_control_callsign="W0NE",
    )
    db.add(s1)
    db.commit()

    db.refresh(season)
    assert len(season.sessions) == 1
    assert season.sessions[0].id == s1.id


def test_create_real_event_session_no_season(db: Session, net: Net):
    session_obj = NetSession(
        net_id=net.id,
        season_id=None,
        start_date=date(2026, 4, 15),
        end_date=None,
        grace_period_hours=24,
        session_type=SessionType.REAL_EVENT,
        status=SessionStatus.SCHEDULED,
        net_control_callsign="W0NE",
    )
    db.add(session_obj)
    db.commit()

    fetched = db.get(NetSession, session_obj.id)
    assert fetched is not None
    assert fetched.session_type == SessionType.REAL_EVENT
    assert fetched.season_id is None
    assert fetched.end_date is None
    assert fetched.season is None
