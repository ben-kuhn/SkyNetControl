# tests/test_scanner_event_routing.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.integrations.scanner.service as scanner_service
from backend.db.base import Base
from backend.modules.events.models import EventMessage, EventType
from backend.modules.events.service import create_event
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
    create_event(db, net_id=net.id, name="E", event_type=EventType.EMERGENCY,
                 created_by="W0NE", activate=True)

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
