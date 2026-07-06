import json
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.integrations.delivery.backends.base import DeliveryResult
from backend.integrations.delivery.service import (
    dispatch_delivery,
    retry_failed,
    get_delivery_status,
)
from backend.config_mgmt.models import AppConfig
from backend.modules.nets.config_service import set_net_config
from backend.db.base import Base

NET_ID = 1


@pytest.fixture
def db_session(app):
    with app.state.session_factory() as session:
        # Ensure a Net row exists with id=NET_ID for per-net config FK
        from backend.modules.nets.models import Net
        if session.get(Net, NET_ID) is None:
            session.add(Net(id=NET_ID, slug="test", name="Test Net"))
            session.commit()
        yield session


def _set_config(db_session, key, value):
    existing = db_session.get(AppConfig, key)
    if existing:
        existing.value = value
    else:
        db_session.add(AppConfig(key=key, value=value))
    db_session.commit()


def _set_net_cfg(db_session, key, value):
    set_net_config(db_session, NET_ID, key, value)


def test_dispatch_creates_logs_and_delivers(db_session):
    _set_net_cfg(db_session, "delivery.backends", json.dumps(["email"]))
    _set_net_cfg(db_session, "delivery.email.to_address", "net@example.com")

    with patch("backend.integrations.delivery.service.get_backend") as mock_get:
        mock_backend = MagicMock()
        mock_backend.send.return_value = DeliveryResult(success=True, error=None)
        mock_get.return_value = mock_backend

        result = dispatch_delivery(db_session, "reminder", 1, "Subject", "Body", NET_ID)

    assert result is True
    logs = db_session.query(DeliveryLog).filter_by(content_type="reminder", content_id=1).all()
    assert len(logs) == 1
    assert logs[0].status == DeliveryStatus.SENT
    assert logs[0].sent_at is not None


def test_dispatch_all_fail_returns_false(db_session):
    _set_net_cfg(db_session, "delivery.backends", json.dumps(["email"]))
    _set_net_cfg(db_session, "delivery.email.to_address", "net@example.com")

    with patch("backend.integrations.delivery.service.get_backend") as mock_get:
        mock_backend = MagicMock()
        mock_backend.send.return_value = DeliveryResult(success=False, error="SMTP down")
        mock_get.return_value = mock_backend

        result = dispatch_delivery(db_session, "reminder", 1, "Subject", "Body", NET_ID)

    assert result is False
    logs = db_session.query(DeliveryLog).filter_by(content_type="reminder", content_id=1).all()
    assert len(logs) == 1
    assert logs[0].status == DeliveryStatus.FAILED
    assert logs[0].error_message == "SMTP down"


def test_dispatch_reuses_existing_row_on_redispatch(db_session):
    """A second dispatch for the same (content, backend) must reset the
    existing row in place — not INSERT a duplicate that would violate the
    UNIQUE(content_type, content_id, backend) constraint. This is what
    powers resend_roster.
    """
    _set_net_cfg(db_session, "delivery.backends", json.dumps(["email"]))
    _set_net_cfg(db_session, "delivery.email.to_address", "net@example.com")

    with patch("backend.integrations.delivery.service.get_backend") as mock_get:
        mock_backend = MagicMock()
        mock_backend.send.return_value = DeliveryResult(success=True, error=None)
        mock_get.return_value = mock_backend

        assert dispatch_delivery(db_session, "roster", 1, "S", "B", NET_ID) is True
        first_log_id = db_session.query(DeliveryLog).filter_by(
            content_type="roster", content_id=1
        ).one().id

        # Re-dispatch (simulates resend_roster) — must succeed and reuse row.
        assert dispatch_delivery(db_session, "roster", 1, "S", "B2", NET_ID) is True

    logs = db_session.query(DeliveryLog).filter_by(content_type="roster", content_id=1).all()
    assert len(logs) == 1
    assert logs[0].id == first_log_id  # same row, reset in place
    assert logs[0].status == DeliveryStatus.SENT


def test_dispatch_redispatch_after_failure_clears_error(db_session):
    """Re-dispatching after a failure must clear the old error_message
    when the retry succeeds, so stale errors don't linger on the row.
    """
    _set_net_cfg(db_session, "delivery.backends", json.dumps(["email"]))
    _set_net_cfg(db_session, "delivery.email.to_address", "net@example.com")

    with patch("backend.integrations.delivery.service.get_backend") as mock_get:
        mock_backend = MagicMock()
        mock_backend.send.return_value = DeliveryResult(success=False, error="SMTP down")
        mock_get.return_value = mock_backend
        dispatch_delivery(db_session, "roster", 2, "S", "B", NET_ID)

        mock_backend.send.return_value = DeliveryResult(success=True, error=None)
        dispatch_delivery(db_session, "roster", 2, "S", "B", NET_ID)

    log = db_session.query(DeliveryLog).filter_by(content_type="roster", content_id=2).one()
    assert log.status == DeliveryStatus.SENT
    assert log.error_message is None


def test_dispatch_no_backends_configured(db_session):
    _set_net_cfg(db_session, "delivery.backends", json.dumps([]))

    result = dispatch_delivery(db_session, "reminder", 1, "Subject", "Body", NET_ID)
    assert result is False


def test_dispatch_multiple_backends_partial_success(db_session):
    _set_net_cfg(db_session, "delivery.backends", json.dumps(["email", "groupsio"]))
    _set_net_cfg(db_session, "delivery.email.to_address", "net@example.com")
    _set_config(db_session, "delivery.groupsio.api_key", "key-123")
    _set_net_cfg(db_session, "delivery.groupsio.group_name", "w0ne")

    call_count = 0

    def mock_get(name):
        mock = MagicMock()
        nonlocal call_count
        if call_count == 0:
            mock.send.return_value = DeliveryResult(success=True, error=None)
        else:
            mock.send.return_value = DeliveryResult(success=False, error="API error")
        call_count += 1
        return mock

    with patch("backend.integrations.delivery.service.get_backend", side_effect=mock_get):
        result = dispatch_delivery(db_session, "reminder", 1, "Subject", "Body", NET_ID)

    assert result is True
    logs = db_session.query(DeliveryLog).filter_by(content_type="reminder", content_id=1).all()
    assert len(logs) == 2
    statuses = {log.status for log in logs}
    assert DeliveryStatus.SENT in statuses
    assert DeliveryStatus.FAILED in statuses


def test_retry_failed_only_retries_failed(db_session):
    _set_net_cfg(db_session, "delivery.email.to_address", "net@example.com")

    db_session.add(
        DeliveryLog(
            content_type="reminder",
            content_id=1,
            backend="email",
            status=DeliveryStatus.SENT,
            created_at=datetime.now(tz=timezone.utc),
            sent_at=datetime.now(tz=timezone.utc),
        )
    )
    db_session.add(
        DeliveryLog(
            content_type="reminder",
            content_id=1,
            backend="groupsio",
            status=DeliveryStatus.FAILED,
            error_message="API error",
            created_at=datetime.now(tz=timezone.utc),
        )
    )
    db_session.commit()

    _set_config(db_session, "delivery.groupsio.api_key", "key-123")
    _set_net_cfg(db_session, "delivery.groupsio.group_name", "w0ne")

    with patch("backend.integrations.delivery.service.get_backend") as mock_get:
        mock_backend = MagicMock()
        mock_backend.send.return_value = DeliveryResult(success=True, error=None)
        mock_get.return_value = mock_backend

        retry_failed(db_session, "reminder", 1, NET_ID)

    logs = db_session.query(DeliveryLog).filter_by(content_type="reminder", content_id=1).all()
    assert all(log.status == DeliveryStatus.SENT for log in logs)


def test_get_delivery_status(db_session):
    db_session.add(
        DeliveryLog(
            content_type="reminder",
            content_id=1,
            backend="email",
            status=DeliveryStatus.SENT,
            created_at=datetime.now(tz=timezone.utc),
            sent_at=datetime.now(tz=timezone.utc),
        )
    )
    db_session.commit()

    logs = get_delivery_status(db_session, "reminder", 1)
    assert len(logs) == 1
    assert logs[0].backend == "email"
    assert logs[0].status == DeliveryStatus.SENT
