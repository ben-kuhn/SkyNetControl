import pytest
from datetime import date, datetime, timezone

from backend.modules.schedule.models import NetSession, NetSeason, SessionType, SessionStatus
from backend.integrations.scanner.service import find_active_session
from backend.db.base import Base


@pytest.fixture
def db_session(app):
    with app.state.session_factory() as session:
        yield session


def _create_session(db_session, start_date, end_date=None, grace_hours=24.0, status=SessionStatus.SCHEDULED):
    season = NetSeason(name="Test", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    db_session.add(season)
    db_session.flush()
    net_session = NetSession(
        season_id=season.id,
        start_date=start_date,
        end_date=end_date,
        grace_period_hours=grace_hours,
        session_type=SessionType.REGULAR_CHECKIN,
        status=status,
    )
    db_session.add(net_session)
    db_session.commit()
    return net_session


def test_find_active_session_during_window(db_session):
    today = date(2026, 5, 20)
    _create_session(db_session, start_date=today, end_date=today, grace_hours=24.0)

    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    result = find_active_session(db_session, now)
    assert result is not None
    assert result.start_date == today


def test_find_active_session_in_grace_before(db_session):
    today = date(2026, 5, 20)
    _create_session(db_session, start_date=today, end_date=today, grace_hours=24.0)

    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    result = find_active_session(db_session, now)
    assert result is not None


def test_find_active_session_in_grace_after(db_session):
    today = date(2026, 5, 20)
    _create_session(db_session, start_date=today, end_date=today, grace_hours=24.0)

    now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    result = find_active_session(db_session, now)
    assert result is not None


def test_find_active_session_outside_window(db_session):
    today = date(2026, 5, 20)
    _create_session(db_session, start_date=today, end_date=today, grace_hours=24.0)

    now = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    result = find_active_session(db_session, now)
    assert result is None


def test_find_active_session_skips_completed(db_session):
    today = date(2026, 5, 20)
    _create_session(db_session, start_date=today, end_date=today, grace_hours=24.0, status=SessionStatus.COMPLETED)

    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    result = find_active_session(db_session, now)
    assert result is None


def test_find_active_session_no_sessions(db_session):
    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    result = find_active_session(db_session, now)
    assert result is None
