import pytest
from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock

from backend.modules.schedule.models import NetSession, NetSeason, SessionType, SessionStatus
from backend.config_mgmt.models import AppConfig
from backend.integrations.scanner.service import run_scan, ScannerState
from backend.db.base import Base


@pytest.fixture
def db_session(app):
    with app.state.session_factory() as session:
        yield session


def _ensure_net(db_session):
    from backend.modules.nets.models import Net
    net = db_session.query(Net).filter_by(slug="t").one_or_none()
    if net is None:
        net = Net(slug="t", name="Test Net")
        db_session.add(net)
        db_session.flush()
    return net


def _create_active_session(db_session):
    net = _ensure_net(db_session)
    season = NetSeason(net_id=net.id, name="Test", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    db_session.add(season)
    db_session.flush()
    net_session = NetSession(
        season_id=season.id,
        start_date=date(2026, 5, 20),
        end_date=date(2026, 5, 20),
        grace_period_hours=24.0,
        session_type=SessionType.REGULAR_CHECKIN,
        status=SessionStatus.SCHEDULED,
    )
    db_session.add(net_session)
    db_session.commit()
    return net_session


def test_run_scan_imports_messages(db_session):
    net_session = _create_active_session(db_session)
    db_session.add(AppConfig(key="net_address", value="w0ne@winlink.org"))
    db_session.add(AppConfig(key="pat_mailbox_path", value="/tmp/fake/mailbox/W0NE"))
    db_session.commit()

    fake_messages = [
        {
            "message_id": "msg1",
            "from_address": "test@winlink.org",
            "to_address": "w0ne@winlink.org",
            "subject": "Check-in",
            "received_at": datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
            "body": "Name: Test User\nCallsign: TSTU\nCity: Denver\nCounty: Denver\nState: CO",
        }
    ]

    with patch("backend.integrations.scanner.service.read_mailbox", return_value=fake_messages) as mock_read:
        with patch("backend.integrations.scanner.service.scan_and_import_messages", return_value=[]) as mock_scan:
            now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
            count = run_scan(db_session, now)

    mock_read.assert_called_once()
    mock_scan.assert_called_once()
    assert count == 0


def test_run_scan_no_active_session(db_session):
    db_session.add(AppConfig(key="net_address", value="w0ne@winlink.org"))
    db_session.add(AppConfig(key="pat_mailbox_path", value="/tmp/fake/mailbox/W0NE"))
    db_session.commit()

    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    count = run_scan(db_session, now)
    assert count is None


def test_run_scan_no_mailbox_path(db_session):
    _create_active_session(db_session)
    db_session.add(AppConfig(key="net_address", value="w0ne@winlink.org"))
    db_session.commit()

    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    count = run_scan(db_session, now)
    assert count is None


def test_scanner_state():
    state = ScannerState()
    assert state.running is False
    assert state.last_scan_time is None
    assert state.last_scan_count is None
