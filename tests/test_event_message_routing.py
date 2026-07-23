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
from backend.modules.events.event_config_service import set_event_config
from backend.modules.events.messages import route_event_messages
from backend.modules.events.service import activate_event, check_in, create_event
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
    event = create_event(db, name=name, event_type=EventType.EMERGENCY, created_by="W0NE")
    return activate_event(db, event.id, actor="W0NE")


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
        draft = create_event(db, name="D", event_type=EventType.EMERGENCY, created_by="W0NE")
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

    # ------------------------------------------------------------------
    # Cross-event address isolation (EP1 multi-concurrent-event scenario)
    # ------------------------------------------------------------------

    def test_cross_event_no_fanout(self, db, net):
        """Two active events with distinct net_address callsigns each receive ONLY
        messages addressed to them — no cross-event mis-routing."""
        e1 = _active_event(db, net, "E-W0NE")
        e2 = _active_event(db, net, "E-W0NC")
        set_event_config(db, e1.id, "net_address", "W0NE@winlink.org")
        set_event_config(db, e2.id, "net_address", "W0NC@winlink.org")

        d1 = {"message_id": "X1", "from_address": "KE0AAA@winlink.org",
               "to_address": "W0NE@winlink.org", "subject": "to-NE", "body": "",
               "received_at": _raw_dict("X1")["received_at"], "path": None}
        d2 = {"message_id": "X2", "from_address": "KE0BBB@winlink.org",
               "to_address": "W0NC@winlink.org", "subject": "to-NC", "body": "",
               "received_at": _raw_dict("X2")["received_at"], "path": None}
        _persist_raw(db, d1)
        _persist_raw(db, d2)

        n = route_event_messages(db, net.id, [d1, d2])
        assert n == 2  # one per event, not four

        e1_msgs = db.query(EventMessage).filter(EventMessage.event_id == e1.id).all()
        e2_msgs = db.query(EventMessage).filter(EventMessage.event_id == e2.id).all()

        assert len(e1_msgs) == 1
        assert e1_msgs[0].to_address == "W0NE@winlink.org"

        assert len(e2_msgs) == 1
        assert e2_msgs[0].to_address == "W0NC@winlink.org"

    def test_catchall_event_receives_all(self, db, net):
        """An active event with NO net_address configured acts as a catch-all and
        receives every inbound message regardless of to_address (backward compat)."""
        event = _active_event(db, net, "Catchall")
        # Deliberately do NOT set net_address — it stays unconfigured.

        d1 = {"message_id": "Y1", "from_address": "KE0AAA@winlink.org",
               "to_address": "W0NE@winlink.org", "subject": "msg1", "body": "",
               "received_at": _raw_dict("Y1")["received_at"], "path": None}
        d2 = {"message_id": "Y2", "from_address": "KE0BBB@winlink.org",
               "to_address": "W0NC@winlink.org", "subject": "msg2", "body": "",
               "received_at": _raw_dict("Y2")["received_at"], "path": None}
        _persist_raw(db, d1)
        _persist_raw(db, d2)

        n = route_event_messages(db, net.id, [d1, d2])
        assert n == 2  # both delivered to the one catch-all event

        msgs = db.query(EventMessage).filter(EventMessage.event_id == event.id).all()
        assert len(msgs) == 2
