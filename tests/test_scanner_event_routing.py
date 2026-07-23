# tests/test_scanner_event_routing.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.integrations.scanner.service as scanner_service
from backend.db.base import Base
from backend.modules.events.models import EventMessage, EventType
from backend.modules.events.service import activate_event, create_event
from backend.modules.nets.config_service import set_net_config_bulk
from tests.conftest import make_test_net


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def _no_callbook(monkeypatch):
    monkeypatch.setattr("backend.modules.events.service.lookup_callsign", lambda db, cs: None)


def test_scan_one_routes_to_active_event(db, monkeypatch):
    net = make_test_net(db)
    set_net_config_bulk(db, net.id, {"net_address": "W0NE@winlink.org"})
    e = create_event(db, name="E", event_type=EventType.EMERGENCY, created_by="W0NE")
    activate_event(db, e.id, actor="W0NE")

    # Stub the mailbox read to return one message; no session window needed for events.
    def fake_read_mailbox(inbox_path, net_address):
        return [{
            "message_id": "M1", "from_address": "KE0XYZ@winlink.org", "to_address": net_address,
            "subject": "SITREP", "body": "all clear", "received_at": datetime.now(timezone.utc),
            "path": None,
        }]
    monkeypatch.setattr(scanner_service, "read_mailbox", fake_read_mailbox)

    # No active NetSession → scan_and_import returns [] but the RawMessage must
    # still be persisted for event routing. Stub scan_and_import to upsert the raw
    # row (mirrors real behavior) and return no check-ins.
    from backend.modules.checkins.models import MessageType, RawMessage

    def fake_scan_and_import(db, messages, session, net_id=None):
        for m in messages:
            db.add(RawMessage(
                message_id=m["message_id"], from_address=m["from_address"],
                received_at=m["received_at"], subject=m["subject"], body=m["body"],
                message_type=MessageType.UNKNOWN, parsed=False,
            ))
        db.commit()
        return []
    monkeypatch.setattr(scanner_service, "scan_and_import_messages", fake_scan_and_import)
    # No active session: make find_active_session return None so the check-in path
    # short-circuits but event routing still runs.
    monkeypatch.setattr(scanner_service, "find_active_session", lambda db, now, net_id=None: None)

    now = datetime.now(timezone.utc)
    scanner_service.scan_one(db, net.id, "/tmp/fake-mailbox", now)

    assert db.query(EventMessage).count() == 1


def test_pre_persisted_message_becomes_checkin_when_session_opens(db):
    """Regression: a RawMessage pre-persisted by _persist_raw_messages (parsed=False)
    must NOT be permanently blocked from becoming a CheckIn when a session opens.
    scan_and_import_messages must reuse the orphan row and create the CheckIn."""
    from datetime import date, time
    from backend.integrations.scanner.service import _persist_raw_messages
    from backend.modules.checkins.models import RawMessage, CheckIn
    from backend.modules.checkins.service import scan_and_import_messages
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType

    net = make_test_net(db, slug="pp1")
    set_net_config_bulk(db, net.id, {"net_address": "W0NE@winlink.org"})

    messages = [{
        "message_id": "PREPERSIST1",
        "from_address": "KA0XYZ@winlink.org",
        "to_address": "W0NE@winlink.org",
        "subject": "Check-in",
        "body": "Name: Alice\nCallsign: KA0XYZ\nCity: Denver\nState: CO\nMode: Winlink\n",
        "received_at": datetime.now(timezone.utc),
        "path": None,
    }]

    # Step 1: persist raw message (simulates event-active, no-session scan)
    _persist_raw_messages(db, messages)

    # Verify it's there with parsed=False
    raw = db.query(RawMessage).filter_by(message_id="PREPERSIST1").one()
    assert raw.parsed is False

    # Step 2: open a session
    season = NetSeason(
        net_id=net.id, name="S",
        start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        day_of_week=3, time=time(18, 0),
    )
    db.add(season)
    db.flush()
    net_session = NetSession(
        season_id=season.id,
        start_date=date.today(),
        end_date=date.today(),
        grace_period_hours=24,
        session_type=SessionType.REGULAR_CHECKIN,
    )
    db.add(net_session)
    db.commit()

    # Step 3: call scan_and_import_messages with the same message
    checkins = scan_and_import_messages(db, messages, net_session, net_id=net.id)

    # A CheckIn must have been created
    assert len(checkins) == 1, "CheckIn must be created from pre-persisted RawMessage"
    assert checkins[0].callsign == "KA0XYZ"

    # The RawMessage row must now be parsed=True
    db.refresh(raw)
    assert raw.parsed is True

    # No duplicate RawMessage row
    count = db.query(RawMessage).filter_by(message_id="PREPERSIST1").count()
    assert count == 1, f"Expected 1 RawMessage row, got {count}"


def test_scan_one_does_not_persist_without_active_event(db, monkeypatch):
    """When no active event exists and no session is open, scan_one must NOT
    persist RawMessage rows — they would permanently block check-in import."""
    import backend.integrations.scanner.service as scanner_service
    from backend.modules.checkins.models import RawMessage

    net = make_test_net(db, slug="pe1")
    set_net_config_bulk(db, net.id, {"net_address": "W0NE@winlink.org"})

    def fake_read_mailbox(inbox_path, net_address):
        return [{
            "message_id": "NOACTIVE1",
            "from_address": "KA0XYZ@winlink.org",
            "to_address": net_address,
            "subject": "Check-in",
            "body": "body",
            "received_at": datetime.now(timezone.utc),
            "path": None,
        }]
    monkeypatch.setattr(scanner_service, "read_mailbox", fake_read_mailbox)
    monkeypatch.setattr(scanner_service, "find_active_session", lambda db, now, net_id=None: None)

    # No active event for this net
    scanner_service.scan_one(db, net.id, "/tmp/fake", datetime.now(timezone.utc))

    # No RawMessage rows should have been persisted
    assert db.query(RawMessage).count() == 0, "Must not persist without active event"


# ---------------------------------------------------------------------------
# Fix A: scan_all_enabled must not skip remote-PAT nets (no mailbox)
# ---------------------------------------------------------------------------


def test_scan_all_enabled_does_not_skip_pat_transport_net(monkeypatch):
    """A net with pat_transport_enabled=true and no pat_mailbox_path must NOT
    be skipped by scan_all_enabled.  The old gate `if not mailbox: continue`
    silently dropped all remote-PAT nets.
    """
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as db:
        net = make_test_net(db, slug="pat-only")
        # Enable scanner but no file mailbox — remote-PAT only.
        set_net_config_bulk(db, net.id, {
            "scanner.enabled": "true",
            "net_address": "W0NE@winlink.org",
            "pat_transport_enabled": "true",
            "pat_http_base_url": "http://pat.local:8080",
        })
        db.commit()

    scanned_net_ids = []

    def fake_scan_one(db, net_id, mailbox, now):
        scanned_net_ids.append(net_id)
        return 0

    monkeypatch.setattr(scanner_service, "scan_one", fake_scan_one)

    with factory() as db:
        result = scanner_service.scan_all_enabled(db, datetime.now(timezone.utc))

    assert scanned_net_ids, "scan_all_enabled must call scan_one for a remote-PAT net"
    engine.dispose()


def test_scan_all_enabled_still_skips_net_with_no_mailbox_no_transport(monkeypatch):
    """A net with scanner.enabled=true but no mailbox and no transport is still skipped."""
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as db:
        net = make_test_net(db, slug="no-transport")
        set_net_config_bulk(db, net.id, {
            "scanner.enabled": "true",
            "net_address": "W0NE@winlink.org",
            # no pat_mailbox_path, no pat_transport_enabled
        })
        db.commit()

    scanned_net_ids = []

    def fake_scan_one(db, net_id, mailbox, now):
        scanned_net_ids.append(net_id)
        return 0

    monkeypatch.setattr(scanner_service, "scan_one", fake_scan_one)

    with factory() as db:
        result = scanner_service.scan_all_enabled(db, datetime.now(timezone.utc))

    assert scanned_net_ids == [], "Must skip net with neither mailbox nor transport"
    engine.dispose()
