import pytest
from datetime import date, datetime as _datetime, time, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.auth.models import User
from backend.modules.nets.config_service import set_net_config
from backend.modules.nets.models import Net
from backend.modules.reminders import nco
from backend.modules.reminders.models import (
    NcoReminderKind,
    NcoReminderLog,
    ReminderLog,
    ReminderStatus,
    ReminderTemplate,
    TemplateType,
)
from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus

TOMRR = date(2026, 9, 21)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


class _FakeDatetime:
    """Stand-in for the real datetime class with a controllable wall-clock time."""

    current = time(8, 0)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return _datetime.now()
        return _datetime.combine(_datetime.now(tz).date(), cls.current, tzinfo=tz)


def _make_net(db, slug="t", name="Test Net"):
    net = Net(slug=slug, name=name)
    db.add(net)
    db.flush()
    return net


def _make_season(db, net_id, *, is_week_long=False):
    season = NetSeason(
        net_id=net_id,
        name="Spring",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 12, 31),
        day_of_week=0,
        time=time(18, 0),
        is_week_long=is_week_long,
    )
    db.add(season)
    db.flush()
    return season


def _make_session(
    db,
    season_id,
    net_id,
    start_date,
    *,
    net_control_callsign="W0NE",
    end_date=None,
    status=SessionStatus.SCHEDULED,
):
    session = NetSession(
        season_id=season_id,
        net_id=net_id,
        start_date=start_date,
        end_date=end_date or (start_date + timedelta(days=1)),
        grace_period_hours=24.0,
        session_type=SessionType.REGULAR_CHECKIN,
        status=status,
        net_control_callsign=net_control_callsign,
    )
    db.add(session)
    db.flush()
    return session


def _make_default_template(db, net_id):
    return ReminderTemplate(
        net_id=net_id,
        name="Default",
        template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net on {{ date }}",
        body_template="Body",
        lead_time_days=2,
        is_default=True,
    )


def _enable(db, net_id, **overrides):
    values = {
        "reminders.nco_email_enabled": "true",
        "reminders.nco_email_timezone": "UTC",
        "reminders.nco_email_morning_time": "07:00",
        "reminders.nco_email_evening_time": "19:00",
    }
    values.update(overrides)
    for key, value in values.items():
        set_net_config(db, net_id, key, value)


@pytest.mark.anyio
async def test_morning_email_sent_once_with_draft_link_and_deduped(db):
    net = _make_net(db)
    season = _make_season(db, net.id)
    session = _make_session(db, season.id, net.id, TOMRR)
    db.add(
        ReminderLog(
            session_id=session.id,
            template_id=None,
            status=ReminderStatus.DRAFT,
            content_subject="Subject",
            content_body="Body",
            drafted_at=_datetime.now(),
        )
    )
    db.add(User(callsign="W0NE", oidc_subject="s", name="NCO", email="nco@example.com"))
    db.commit()
    _enable(db, net.id)

    send_mock = AsyncMock()
    with patch.object(nco, "send_email", send_mock), \
         patch.object(nco, "_today", return_value=date(2026, 9, 20)), \
         patch.object(nco, "datetime", _FakeDatetime):
        _FakeDatetime.current = time(8, 0)
        await nco.run_nco_reminder_checks(db)

    assert send_mock.call_count == 1
    to, subject, body = send_mock.await_args.args[1], send_mock.await_args.args[2], send_mock.await_args.args[3]
    assert to == "nco@example.com"
    assert "is ready" in subject
    assert "focus=" in body  # deep-links to the auto-generated draft

    log = db.query(NcoReminderLog).filter_by(session_id=session.id, kind=NcoReminderKind.MORNING).first()
    assert log is not None

    # Second tick does not re-send
    send_mock.reset_mock()
    await nco.run_nco_reminder_checks(db)
    send_mock.assert_not_called()


@pytest.mark.anyio
async def test_evening_email_fires_when_unsent(db):
    net = _make_net(db)
    season = _make_season(db, net.id)
    session = _make_session(db, season.id, net.id, TOMRR)
    db.add(_make_default_template(db, net.id))
    db.add(User(callsign="W0NE", oidc_subject="s", name="NCO", email="nco@example.com"))
    db.commit()
    _enable(db, net.id)

    send_mock = AsyncMock()
    with patch.object(nco, "send_email", send_mock), \
         patch.object(nco, "_today", return_value=date(2026, 9, 20)), \
         patch.object(nco, "datetime", _FakeDatetime):
        _FakeDatetime.current = time(20, 0)
        await nco.run_nco_reminder_checks(db)

    assert send_mock.call_count == 1
    subject = send_mock.await_args.args[2]
    assert "not been sent" in subject

    log = db.query(NcoReminderLog).filter_by(session_id=session.id, kind=NcoReminderKind.EVENING).first()
    assert log is not None


@pytest.mark.anyio
async def test_no_email_when_reminder_sent(db):
    net = _make_net(db)
    season = _make_season(db, net.id)
    session = _make_session(db, season.id, net.id, TOMRR)
    db.add(
        ReminderLog(
            session_id=session.id,
            template_id=None,
            status=ReminderStatus.SENT,
            content_subject="S",
            content_body="B",
            drafted_at=_datetime.now(),
            sent_at=_datetime.now(),
        )
    )
    db.add(User(callsign="W0NE", oidc_subject="s", name="NCO", email="nco@example.com"))
    db.commit()
    _enable(db, net.id)

    send_mock = AsyncMock()
    with patch.object(nco, "send_email", send_mock), \
         patch.object(nco, "_today", return_value=date(2026, 9, 20)), \
         patch.object(nco, "datetime", _FakeDatetime):
        for current in (time(8, 0), time(20, 0)):
            _FakeDatetime.current = current
            await nco.run_nco_reminder_checks(db)

    send_mock.assert_not_called()


@pytest.mark.anyio
async def test_no_email_when_reminder_skipped(db):
    net = _make_net(db)
    season = _make_season(db, net.id)
    session = _make_session(db, season.id, net.id, TOMRR)
    db.add(
        ReminderLog(
            session_id=session.id,
            template_id=None,
            status=ReminderStatus.SKIPPED,
            content_subject="S",
            content_body="B",
            drafted_at=_datetime.now(),
        )
    )
    db.add(User(callsign="W0NE", oidc_subject="s", name="NCO", email="nco@example.com"))
    db.commit()
    _enable(db, net.id)

    send_mock = AsyncMock()
    with patch.object(nco, "send_email", send_mock), \
         patch.object(nco, "_today", return_value=date(2026, 9, 20)), \
         patch.object(nco, "datetime", _FakeDatetime):
        _FakeDatetime.current = time(8, 0)
        await nco.run_nco_reminder_checks(db)

    send_mock.assert_not_called()


@pytest.mark.anyio
async def test_week_long_sessions_never_emailed(db):
    net = _make_net(db)
    season = _make_season(db, net.id, is_week_long=True)
    session = _make_session(db, season.id, net.id, TOMRR, end_date=TOMRR + timedelta(days=6))
    db.add(_make_default_template(db, net.id))
    db.add(User(callsign="W0NE", oidc_subject="s", name="NCO", email="nco@example.com"))
    db.commit()
    _enable(db, net.id)

    send_mock = AsyncMock()
    with patch.object(nco, "send_email", send_mock), \
         patch.object(nco, "_today", return_value=date(2026, 9, 20)), \
         patch.object(nco, "datetime", _FakeDatetime):
        _FakeDatetime.current = time(8, 0)
        await nco.run_nco_reminder_checks(db)

    send_mock.assert_not_called()


@pytest.mark.anyio
async def test_non_tomorrow_sessions_skipped(db):
    net = _make_net(db)
    season = _make_season(db, net.id)
    _make_session(db, season.id, net.id, date(2026, 9, 20))  # today
    _make_session(db, season.id, net.id, date(2026, 9, 19))  # yesterday
    _make_session(db, season.id, net.id, date(2026, 9, 24))  # 4 days out
    db.add(_make_default_template(db, net.id))
    db.add(User(callsign="W0NE", oidc_subject="s", name="NCO", email="nco@example.com"))
    db.commit()
    _enable(db, net.id)

    send_mock = AsyncMock()
    with patch.object(nco, "send_email", send_mock), \
         patch.object(nco, "_today", return_value=date(2026, 9, 20)), \
         patch.object(nco, "datetime", _FakeDatetime):
        _FakeDatetime.current = time(8, 0)
        await nco.run_nco_reminder_checks(db)

    send_mock.assert_not_called()


@pytest.mark.anyio
async def test_time_windows(db):
    net = _make_net(db)
    season = _make_season(db, net.id)
    session = _make_session(db, season.id, net.id, TOMRR)
    db.add(_make_default_template(db, net.id))
    db.add(User(callsign="W0NE", oidc_subject="s", name="NCO", email="nco@example.com"))
    db.commit()
    _enable(db, net.id)

    send_mock = AsyncMock()
    with patch.object(nco, "send_email", send_mock), \
         patch.object(nco, "_today", return_value=date(2026, 9, 20)), \
         patch.object(nco, "datetime", _FakeDatetime):
        # Before morning: nothing
        _FakeDatetime.current = time(6, 0)
        await nco.run_nco_reminder_checks(db)
        send_mock.assert_not_called()

        # Within morning window: morning email
        _FakeDatetime.current = time(10, 0)
        await nco.run_nco_reminder_checks(db)
        assert send_mock.call_count == 1
        assert "is ready" in send_mock.await_args.args[2]

        # Same session again mid-afternoon: still only morning window, deduped
        send_mock.reset_mock()
        _FakeDatetime.current = time(18, 0)
        await nco.run_nco_reminder_checks(db)
        send_mock.assert_not_called()

        # A fresh session (no log yet) mid-afternoon still gets the morning email
        session2 = _make_session(db, season.id, net.id, TOMRR)
        db.commit()
        await nco.run_nco_reminder_checks(db)
        assert send_mock.call_count == 1
        assert "is ready" in send_mock.await_args.args[2]

        # After evening on a fresh session: evening email only
        session3 = _make_session(db, season.id, net.id, TOMRR)
        db.commit()
        _FakeDatetime.current = time(20, 0)
        await nco.run_nco_reminder_checks(db)
        # session2's morning (1) + evening for session1, session2 and session3 (3)
        assert send_mock.call_count == 4
        assert "not been sent" in send_mock.await_args.args[2]

    assert db.query(NcoReminderLog).filter_by(session_id=session.id, kind=NcoReminderKind.MORNING).count() == 1
    assert db.query(NcoReminderLog).filter_by(session_id=session2.id, kind=NcoReminderKind.MORNING).count() == 1
    assert db.query(NcoReminderLog).filter_by(session_id=session3.id, kind=NcoReminderKind.EVENING).count() == 1


@pytest.mark.anyio
async def test_recipient_resolution_override_wins(db):
    net = _make_net(db)
    season = _make_season(db, net.id)
    _make_session(db, season.id, net.id, TOMRR)
    db.add(_make_default_template(db, net.id))
    db.add(User(callsign="W0NE", oidc_subject="s", name="NCO", email="nco@example.com"))
    db.commit()
    _enable(db, net.id, **{"reminders.nco_email_to": "override@example.com"})

    send_mock = AsyncMock()
    with patch.object(nco, "send_email", send_mock), \
         patch.object(nco, "_today", return_value=date(2026, 9, 20)), \
         patch.object(nco, "datetime", _FakeDatetime):
        _FakeDatetime.current = time(8, 0)
        await nco.run_nco_reminder_checks(db)

    assert send_mock.call_count == 1
    assert send_mock.await_args.args[1] == "override@example.com"


@pytest.mark.anyio
async def test_no_email_when_recipient_unresolvable(db):
    net = _make_net(db)
    season = _make_season(db, net.id)
    # Session NCO has no User row, so no email and no override
    _make_session(db, season.id, net.id, TOMRR, net_control_callsign="KD0NONE")
    db.add(_make_default_template(db, net.id))
    db.commit()
    _enable(db, net.id)

    send_mock = AsyncMock()
    with patch.object(nco, "send_email", send_mock), \
         patch.object(nco, "_today", return_value=date(2026, 9, 20)), \
         patch.object(nco, "datetime", _FakeDatetime):
        _FakeDatetime.current = time(8, 0)
        await nco.run_nco_reminder_checks(db)

    send_mock.assert_not_called()
    assert db.query(NcoReminderLog).count() == 0