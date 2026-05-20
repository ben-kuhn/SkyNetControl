import pytest
from datetime import datetime, timezone

from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.db.base import Base


@pytest.fixture
def db_session(app):
    with app.state.session_factory() as session:
        yield session


def test_delivery_status_enum_values():
    assert DeliveryStatus.PENDING.value == "pending"
    assert DeliveryStatus.SENT.value == "sent"
    assert DeliveryStatus.FAILED.value == "failed"


def test_create_delivery_log(db_session):
    log = DeliveryLog(
        content_type="reminder",
        content_id=1,
        backend="email",
        status=DeliveryStatus.PENDING,
        created_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)

    assert log.id is not None
    assert log.content_type == "reminder"
    assert log.content_id == 1
    assert log.backend == "email"
    assert log.status == DeliveryStatus.PENDING
    assert log.error_message is None
    assert log.sent_at is None


def test_delivery_log_unique_constraint(db_session):
    """Only one attempt per backend per piece of content."""
    log1 = DeliveryLog(
        content_type="reminder",
        content_id=1,
        backend="email",
        status=DeliveryStatus.PENDING,
        created_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(log1)
    db_session.commit()

    log2 = DeliveryLog(
        content_type="reminder",
        content_id=1,
        backend="email",
        status=DeliveryStatus.PENDING,
        created_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(log2)
    with pytest.raises(Exception):
        db_session.commit()


def test_different_backends_same_content(db_session):
    """Different backends for same content are allowed."""
    for backend_name in ("email", "groupsio", "winlink"):
        log = DeliveryLog(
            content_type="reminder",
            content_id=1,
            backend=backend_name,
            status=DeliveryStatus.PENDING,
            created_at=datetime.now(tz=timezone.utc),
        )
        db_session.add(log)
    db_session.commit()

    logs = db_session.query(DeliveryLog).filter_by(content_type="reminder", content_id=1).all()
    assert len(logs) == 3
