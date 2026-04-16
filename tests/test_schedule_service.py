import pytest
from datetime import date, time
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionType,
    SessionStatus,
)
from backend.modules.schedule.service import generate_sessions


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_generate_weekly_sessions(db: Session):
    season = NetSeason(
        name="Test Season",
        start_date=date(2026, 9, 3),  # Thursday
        end_date=date(2026, 10, 1),  # Thursday (4 weeks)
        day_of_week=3,  # Thursday (0=Monday)
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()

    sessions = generate_sessions(db, season, default_net_control="W0NE")

    assert len(sessions) == 5  # Sep 3, 10, 17, 24, Oct 1
    # First session is regular, second is activity, alternating
    assert sessions[0].session_type == SessionType.REGULAR_CHECKIN
    assert sessions[1].session_type == SessionType.ACTIVITY
    assert sessions[2].session_type == SessionType.REGULAR_CHECKIN
    assert sessions[3].session_type == SessionType.ACTIVITY
    assert sessions[4].session_type == SessionType.REGULAR_CHECKIN

    for s in sessions:
        assert s.status == SessionStatus.SCHEDULED
        assert s.net_control_callsign == "W0NE"
        assert s.season_id == season.id


def test_generate_sessions_correct_dates(db: Session):
    season = NetSeason(
        name="Short Season",
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 17),
        day_of_week=3,
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()

    sessions = generate_sessions(db, season, default_net_control="W0NE")

    assert len(sessions) == 3
    assert sessions[0].start_date == date(2026, 9, 3)
    assert sessions[1].start_date == date(2026, 9, 10)
    assert sessions[2].start_date == date(2026, 9, 17)


def test_generate_sessions_default_grace_period(db: Session):
    season = NetSeason(
        name="Test",
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 3),
        day_of_week=3,
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()

    sessions = generate_sessions(db, season, default_net_control="W0NE")

    assert len(sessions) == 1
    assert sessions[0].grace_period_hours == 24.0


def test_generate_week_long_sessions(db: Session):
    season = NetSeason(
        name="Summer 2026",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 21),
        day_of_week=None,
        time=None,
        is_week_long=True,
        activity_cadence=1,
    )
    db.add(season)
    db.commit()

    sessions = generate_sessions(db, season, default_net_control="W0NE")

    assert len(sessions) == 3  # 3 weeks: Jun 1-7, Jun 8-14, Jun 15-21
    assert sessions[0].start_date == date(2026, 6, 1)
    assert sessions[0].end_date == date(2026, 6, 7)
    assert sessions[1].start_date == date(2026, 6, 8)
    assert sessions[1].end_date == date(2026, 6, 14)
    assert sessions[2].start_date == date(2026, 6, 15)
    assert sessions[2].end_date == date(2026, 6, 21)
