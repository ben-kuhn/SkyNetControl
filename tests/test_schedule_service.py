import pytest
from datetime import date, time
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.schedule.models import (
    NetSeason,
    SessionType,
    SessionStatus,
)
from backend.modules.schedule.service import (
    generate_sessions,
    create_session,
    get_session,
    list_sessions,
    update_session,
)


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

    sessions = generate_sessions(db, season)

    assert len(sessions) == 5  # Sep 3, 10, 17, 24, Oct 1
    # First session is regular, second is activity, alternating
    assert sessions[0].session_type == SessionType.REGULAR_CHECKIN
    assert sessions[1].session_type == SessionType.ACTIVITY
    assert sessions[2].session_type == SessionType.REGULAR_CHECKIN
    assert sessions[3].session_type == SessionType.ACTIVITY
    assert sessions[4].session_type == SessionType.REGULAR_CHECKIN

    for s in sessions:
        assert s.status == SessionStatus.SCHEDULED
        # No NCO passed in → auto-generated sessions ship unassigned.
        assert s.net_control_callsign is None
        assert s.season_id == season.id


def test_generate_sessions_with_default_nco(db: Session):
    """Operator picks a per-season default NCO on the create form; every
    auto-generated session is stamped with it. Nets that rotate NCOs
    leave it None (see test_generate_sessions above)."""
    season = NetSeason(
        name="Fall 2026",
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 17),
        day_of_week=3,
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()

    sessions = generate_sessions(db, season, default_net_control="KD0NCO")

    assert len(sessions) >= 1
    for s in sessions:
        assert s.net_control_callsign == "KD0NCO"


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

    sessions = generate_sessions(db, season)

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

    sessions = generate_sessions(db, season)

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

    sessions = generate_sessions(db, season)

    assert len(sessions) == 3  # 3 weeks: Jun 1-7, Jun 8-14, Jun 15-21
    assert sessions[0].start_date == date(2026, 6, 1)
    assert sessions[0].end_date == date(2026, 6, 7)
    assert sessions[1].start_date == date(2026, 6, 8)
    assert sessions[1].end_date == date(2026, 6, 14)
    assert sessions[2].start_date == date(2026, 6, 15)
    assert sessions[2].end_date == date(2026, 6, 21)


def test_create_adhoc_session(db: Session):
    session_obj = create_session(
        db,
        start_date=date(2026, 4, 15),
        session_type=SessionType.REGULAR_CHECKIN,
        net_control_callsign="W0NE",
    )
    assert session_obj.id is not None
    assert session_obj.season_id is None
    assert session_obj.end_date is None
    assert session_obj.session_type == SessionType.REGULAR_CHECKIN
    assert session_obj.status == SessionStatus.SCHEDULED
    assert session_obj.grace_period_hours == 24.0


def test_create_real_event_session(db: Session):
    session_obj = create_session(
        db,
        start_date=date(2026, 4, 15),
        session_type=SessionType.REAL_EVENT,
        net_control_callsign="W0NE",
    )
    assert session_obj.session_type == SessionType.REAL_EVENT
    assert session_obj.season_id is None
    assert session_obj.end_date is None


def test_get_session(db: Session):
    created = create_session(
        db,
        start_date=date(2026, 4, 15),
        session_type=SessionType.REGULAR_CHECKIN,
    )
    fetched = get_session(db, created.id)
    assert fetched is not None
    assert fetched.id == created.id

    missing = get_session(db, 9999)
    assert missing is None


def test_list_sessions_no_filter(db: Session):
    season = NetSeason(
        name="Test",
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 10),
        day_of_week=3,
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()
    generate_sessions(db, season)

    create_session(
        db,
        start_date=date(2026, 4, 15),
        session_type=SessionType.REAL_EVENT,
    )

    all_sessions = list_sessions(db)
    assert len(all_sessions) == 3  # 2 from season + 1 ad-hoc


def test_list_sessions_filter_by_season(db: Session):
    season = NetSeason(
        name="Test",
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 10),
        day_of_week=3,
        time=time(19, 0),
        is_week_long=False,
        activity_cadence=2,
    )
    db.add(season)
    db.commit()
    generate_sessions(db, season)

    create_session(
        db,
        start_date=date(2026, 4, 15),
        session_type=SessionType.REAL_EVENT,
    )

    season_sessions = list_sessions(db, season_id=season.id)
    assert len(season_sessions) == 2
    for s in season_sessions:
        assert s.season_id == season.id


def test_list_sessions_filter_by_status(db: Session):
    s1 = create_session(
        db,
        start_date=date(2026, 4, 15),
        session_type=SessionType.REAL_EVENT,
    )
    create_session(
        db,
        start_date=date(2026, 4, 16),
        session_type=SessionType.REGULAR_CHECKIN,
    )

    s1.status = SessionStatus.CANCELLED
    db.commit()

    cancelled = list_sessions(db, status=SessionStatus.CANCELLED)
    assert len(cancelled) == 1
    assert cancelled[0].id == s1.id


def test_update_session(db: Session):
    session_obj = create_session(
        db,
        start_date=date(2026, 4, 15),
        session_type=SessionType.REAL_EVENT,
    )

    updated = update_session(
        db,
        session_obj.id,
        status=SessionStatus.COMPLETED,
        end_date=date(2026, 4, 17),
        net_control_callsign="W0NE",
    )
    assert updated is not None
    assert updated.status == SessionStatus.COMPLETED
    assert updated.end_date == date(2026, 4, 17)
    assert updated.net_control_callsign == "W0NE"


def test_update_session_not_found(db: Session):
    result = update_session(db, 9999, status=SessionStatus.COMPLETED)
    assert result is None
