from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.checkins.models import MessageType, RawMessage
from backend.modules.events.models import (
    Event,
    EventLogEntry,
    EventLogType,
    EventMessage,
    EventStatus,
    EventType,
    MessageDirection,
    MessageStatus,
)
from backend.modules.events.messages import route_event_messages
from backend.modules.events.service import check_in, create_event
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


@pytest.fixture
def net(db):
    return make_test_net(db)


def _raw_dict(mid, frm="KE0XYZ@winlink.org", subj="SITREP", body="all clear"):
    return {
        "message_id": mid, "from_address": frm, "to_address": "W0NE@winlink.org",
        "subject": subj, "body": body, "received_at": datetime.now(timezone.utc), "path": None,
    }


def _persist_raw(db, d):
    raw = RawMessage(
        message_id=d["message_id"], from_address=d["from_address"], received_at=d["received_at"],
        subject=d["subject"], body=d["body"], message_type=MessageType.UNKNOWN, parsed=False,
    )
    db.add(raw)
    db.commit()
    return raw


def _active_event(db, net, name="E"):
    return create_event(db, net_id=net.id, name=name, event_type=EventType.EMERGENCY,
                        created_by="W0NE", activate=True)


class TestRouting:
    def test_routes_inbound_to_active_event(self, db, net):
        event = _active_event(db, net)
        d = _raw_dict("M1")
        _persist_raw(db, d)
        n = route_event_messages(db, net.id, [d])
        assert n == 1
        msg = db.query(EventMessage).one()
        assert msg.event_id == event.id
        assert msg.direction == MessageDirection.INBOUND
        assert msg.status == MessageStatus.UNREAD
        assert msg.from_callsign == "KE0XYZ"
        assert msg.msg_seq == 1

    def test_participant_linked_by_callsign(self, db, net):
        event = _active_event(db, net)
        check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        d = _raw_dict("M1", frm="ke0xyz-7@winlink.org")  # SSID + case → same participant
        _persist_raw(db, d)
        route_event_messages(db, net.id, [d])
        msg = db.query(EventMessage).one()
        assert msg.participant_id is not None

    def test_unmatched_sender_leaves_participant_null(self, db, net):
        _active_event(db, net)
        d = _raw_dict("M1", frm="W0OUT@winlink.org")
        _persist_raw(db, d)
        route_event_messages(db, net.id, [d])
        assert db.query(EventMessage).one().participant_id is None

    def test_breadcrumb_written_to_log(self, db, net):
        event = _active_event(db, net)
        d = _raw_dict("M1", subj="Road closed")
        _persist_raw(db, d)
        route_event_messages(db, net.id, [d])
        entry = (
            db.query(EventLogEntry)
            .filter(EventLogEntry.event_id == event.id, EventLogEntry.entry_type == EventLogType.SYSTEM)
            .order_by(EventLogEntry.seq.desc())
            .first()
        )
        assert "KE0XYZ" in entry.message
        assert "Road closed" in entry.message
        assert entry.callsign == "KE0XYZ"

    def test_dedup_on_rerun(self, db, net):
        _active_event(db, net)
        d = _raw_dict("M1")
        _persist_raw(db, d)
        assert route_event_messages(db, net.id, [d]) == 1
        assert route_event_messages(db, net.id, [d]) == 0  # second run: no dupes
        assert db.query(EventMessage).count() == 1

    def test_fans_out_to_all_active_events(self, db, net):
        e1 = _active_event(db, net, "E1")
        e2 = _active_event(db, net, "E2")
        d = _raw_dict("M1")
        _persist_raw(db, d)
        n = route_event_messages(db, net.id, [d])
        assert n == 2
        assert {m.event_id for m in db.query(EventMessage).all()} == {e1.id, e2.id}

    def test_skips_non_active_events(self, db, net):
        draft = create_event(db, net_id=net.id, name="D", event_type=EventType.EMERGENCY, created_by="W0NE")
        assert draft.status == EventStatus.DRAFT
        d = _raw_dict("M1")
        _persist_raw(db, d)
        assert route_event_messages(db, net.id, [d]) == 0

    def test_missing_raw_message_row_skipped(self, db, net):
        _active_event(db, net)
        d = _raw_dict("M1")
        # Note: NOT persisted — routing must resolve the RawMessage by message_id
        # and skip if absent (check-in pass persists it; if it didn't, skip).
        assert route_event_messages(db, net.id, [d]) == 0
