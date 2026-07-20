from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
import backend.integrations.delivery.models  # noqa: F401 — register DeliveryLog
import backend.integrations.winlink.models  # noqa: F401 — register PatConnectionSession
from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.integrations.winlink.models import PatConnectionSession, PatSessionStatus


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_queued_status_exists():
    assert DeliveryStatus.QUEUED.value == "queued"


def test_session_row_roundtrip():
    db = _db()
    s = PatConnectionSession(
        net_id=1, event_id=None, connect_url="telnet:///",
        method_label="alias: telnet", status=PatSessionStatus.CONNECTING,
        sent_count=0, received_count=0, events=[], actor="W0NC",
        started_at=datetime.now(tz=timezone.utc),
    )
    db.add(s)
    db.commit()
    got = db.query(PatConnectionSession).one()
    assert got.status == PatSessionStatus.CONNECTING
    assert got.events == []


def test_delivery_log_pat_columns():
    db = _db()
    log = DeliveryLog(
        content_type="event_message", content_id=1, backend="winlink",
        status=DeliveryStatus.QUEUED, created_at=datetime.now(tz=timezone.utc),
        pat_session_id=None, pat_mid="ABC123",
    )
    db.add(log)
    db.commit()
    assert db.query(DeliveryLog).one().pat_mid == "ABC123"


def test_session_status_stored_as_lowercase_value():
    """Verify that PatConnectionSession.status stores the enum VALUE ('connecting'),
    not the Python name ('CONNECTING'), consistent with the String(20) migration column."""
    db = _db()
    s = PatConnectionSession(
        net_id=1, event_id=None, connect_url="telnet:///",
        method_label="alias: telnet", status=PatSessionStatus.CONNECTING,
        sent_count=0, received_count=0, events=[], actor="W0NC",
        started_at=datetime.now(tz=timezone.utc),
    )
    db.add(s)
    db.commit()
    raw = db.execute(text("SELECT status FROM pat_connection_sessions")).scalar()
    assert raw == "connecting", f"expected 'connecting', got {raw!r}"
