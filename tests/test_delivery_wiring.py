"""Tests that mark_sent() on reminders and rosters dispatches via delivery backends."""

import json
from datetime import date, datetime, time, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config_mgmt.models import AppConfig
from backend.db.base import Base
from backend.integrations.delivery.backends.base import DeliveryResult
from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.modules.reminders.models import ReminderLog, ReminderStatus
from backend.modules.roster.models import RosterLog, RosterStatus
from backend.modules.schedule.models import (
    NetSeason,
    NetSession,
    SessionStatus,
    SessionType,
)



@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def _ensure_net(db: Session):
    from backend.modules.nets.models import Net
    net = db.query(Net).filter_by(slug="t").one_or_none()
    if net is None:
        net = Net(slug="t", name="Test Net")
        db.add(net)
        db.flush()
    return net


def _setup_session(db: Session) -> NetSession:
    net = _ensure_net(db)
    season = NetSeason(
        net_id=net.id,
        name="Test Season",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        day_of_week=3,
        time=time(18, 0),
    )
    db.add(season)
    db.flush()
    session = NetSession(
        season_id=season.id,
        start_date=date(2026, 5, 20),
        session_type=SessionType.REGULAR_CHECKIN,
        status=SessionStatus.SCHEDULED,
    )
    db.add(session)
    db.commit()
    return session


def _configure_email_backend(db: Session) -> None:
    db.add(AppConfig(key="delivery.backends", value=json.dumps(["email"])))
    db.add(AppConfig(key="delivery.email.to_address", value="net@test.com"))
    db.commit()


class _StubBackend:
    def __init__(self, result: DeliveryResult):
        self._result = result

    def send(self, subject: str, body: str, config: dict) -> DeliveryResult:
        return self._result


def test_reminder_mark_sent_dispatches_delivery(db):
    session = _setup_session(db)
    log = ReminderLog(
        session_id=session.id,
        status=ReminderStatus.APPROVED,
        content_subject="Reminder Subject",
        content_body="Reminder Body",
        drafted_at=datetime.now(tz=timezone.utc),
        approved_at=datetime.now(tz=timezone.utc),
        approved_by="ADMIN",
    )
    db.add(log)
    _configure_email_backend(db)
    db.refresh(log)

    with patch(
        "backend.integrations.delivery.service.get_backend",
        return_value=_StubBackend(DeliveryResult(success=True, error=None)),
    ):
        from backend.modules.reminders.service import mark_sent

        result = mark_sent(db, log.id)

    assert result is not None
    assert result.status == ReminderStatus.SENT
    delivery_logs = db.query(DeliveryLog).filter_by(content_type="reminder", content_id=log.id).all()
    assert len(delivery_logs) == 1
    assert delivery_logs[0].status == DeliveryStatus.SENT


def test_reminder_mark_sent_stays_approved_on_all_fail(db):
    session = _setup_session(db)
    log = ReminderLog(
        session_id=session.id,
        status=ReminderStatus.APPROVED,
        content_subject="Subject",
        content_body="Body",
        drafted_at=datetime.now(tz=timezone.utc),
        approved_at=datetime.now(tz=timezone.utc),
        approved_by="ADMIN",
    )
    db.add(log)
    _configure_email_backend(db)
    db.refresh(log)

    with patch(
        "backend.integrations.delivery.service.get_backend",
        return_value=_StubBackend(DeliveryResult(success=False, error="SMTP down")),
    ):
        from backend.modules.reminders.service import mark_sent

        result = mark_sent(db, log.id)

    assert result is None
    db.refresh(log)
    assert log.status == ReminderStatus.APPROVED


def test_roster_mark_sent_dispatches_delivery(db):
    session = _setup_session(db)
    log = RosterLog(
        session_id=session.id,
        status=RosterStatus.APPROVED,
        content_subject="Roster Subject",
        content_header="Header",
        content_welcome="Welcome",
        content_comments="Comments",
        content_footer="Footer",
        drafted_at=datetime.now(tz=timezone.utc),
        approved_at=datetime.now(tz=timezone.utc),
        approved_by="ADMIN",
    )
    db.add(log)
    _configure_email_backend(db)
    db.refresh(log)

    with patch(
        "backend.integrations.delivery.service.get_backend",
        return_value=_StubBackend(DeliveryResult(success=True, error=None)),
    ):
        from backend.modules.roster.service import mark_sent

        result = mark_sent(db, log.id)

    assert result is not None
    assert result.status == RosterStatus.SENT
    delivery_logs = db.query(DeliveryLog).filter_by(content_type="roster", content_id=log.id).all()
    assert len(delivery_logs) == 1
    assert delivery_logs[0].status == DeliveryStatus.SENT


def test_reminder_send_failure_creates_delivery_failure_notification(db):
    from backend.auth.models import User
    from backend.modules.reminders.service import mark_sent as reminder_mark_sent
    from backend.modules.reminders.models import ReminderLog, ReminderStatus
    from backend.modules.notifications.models import Notification, NotificationKind

    net = _ensure_net(db)
    season = NetSeason(
        net_id=net.id, name="S", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), day_of_week=3, time=time(18, 0)
    )
    db.add(season)
    db.flush()
    sess = NetSession(
        season_id=season.id,
        start_date=date(2026, 5, 28),
        session_type=SessionType.REGULAR_CHECKIN,
        status=SessionStatus.SCHEDULED,
        net_control_callsign="W0NE",
    )
    db.add(sess)
    db.add(User(callsign="W0NE", oidc_subject="x", name="N", ))
    db.flush()

    log = ReminderLog(
        session_id=sess.id,
        status=ReminderStatus.APPROVED,
        content_subject="S",
        content_body="B",
        drafted_at=datetime.now(tz=timezone.utc),
        approved_at=datetime.now(tz=timezone.utc),
        approved_by="W0NE",
    )
    db.add(log)
    db.commit()

    with patch(
        "backend.integrations.delivery.service.dispatch_delivery",
        return_value=False,
    ):
        result = reminder_mark_sent(db, log.id)

    assert result is None
    rows = db.query(Notification).filter(Notification.kind == NotificationKind.DELIVERY_FAILURE).all()
    assert len(rows) == 1
    assert "verify delivery backends" in rows[0].message.lower()
    assert rows[0].link_url == "/config"


def test_roster_send_failure_creates_delivery_failure_notification(db):
    from backend.auth.models import User
    from backend.modules.roster.service import mark_sent as roster_mark_sent
    from backend.modules.roster.models import RosterLog, RosterStatus
    from backend.modules.notifications.models import Notification, NotificationKind

    net = _ensure_net(db)
    season = NetSeason(
        net_id=net.id, name="S", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), day_of_week=3, time=time(18, 0)
    )
    db.add(season)
    db.flush()
    sess = NetSession(
        season_id=season.id,
        start_date=date(2026, 5, 28),
        session_type=SessionType.REGULAR_CHECKIN,
        status=SessionStatus.SCHEDULED,
        net_control_callsign="W0NE",
    )
    db.add(sess)
    db.add(User(callsign="W0NE", oidc_subject="x", name="N", ))
    db.flush()

    log = RosterLog(
        session_id=sess.id,
        status=RosterStatus.APPROVED,
        content_subject="S",
        content_header="H",
        content_welcome="W",
        content_comments="C",
        content_footer="F",
        drafted_at=datetime.now(tz=timezone.utc),
        approved_at=datetime.now(tz=timezone.utc),
        approved_by="W0NE",
    )
    db.add(log)
    db.commit()

    with patch(
        "backend.integrations.delivery.service.dispatch_delivery",
        return_value=False,
    ):
        result = roster_mark_sent(db, log.id)

    assert result is None
    rows = db.query(Notification).filter(Notification.kind == NotificationKind.DELIVERY_FAILURE).all()
    assert len(rows) == 1
    assert rows[0].link_url == "/config"
