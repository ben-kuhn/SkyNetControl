import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.checkins.models import RawMessage, MessageType
from backend.modules.events.models import (
    Event,
    EventMessage,
    EventType,
    MessageDirection,
    MessageStatus,
)
from tests.conftest import make_test_net


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def event(db):
    net = make_test_net(db)
    event = Event(net_id=net.id, name="Tornado", event_type=EventType.EMERGENCY, created_by="W0NE")
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _raw(db, message_id="M1"):
    from datetime import datetime, timezone
    raw = RawMessage(
        message_id=message_id, from_address="KE0XYZ@winlink.org",
        received_at=datetime.now(timezone.utc), subject="SITREP", body="all clear",
        message_type=MessageType.UNKNOWN, parsed=False,
    )
    db.add(raw)
    db.commit()
    db.refresh(raw)
    return raw


def test_event_msg_seq_default(db, event):
    assert event.msg_seq == 0


def test_inbound_message_defaults(db, event):
    raw = _raw(db)
    m = EventMessage(
        event_id=event.id, msg_seq=1, direction=MessageDirection.INBOUND,
        raw_message_id=raw.id, from_callsign="KE0XYZ", to_address="W0NE@winlink.org",
        subject="SITREP", body="all clear",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    assert m.status == MessageStatus.UNREAD
    assert m.participant_id is None
    assert m.reply_to_id is None
    assert m.actor is None
    assert m.created_at is not None


def test_dedup_same_raw_same_event(db, event):
    raw = _raw(db)
    for seq in (1, 2):
        db.add(EventMessage(
            event_id=event.id, msg_seq=seq, direction=MessageDirection.INBOUND,
            raw_message_id=raw.id, from_callsign="KE0XYZ", to_address="W0NE",
            subject="s", body="b",
        ))
        if seq == 1:
            db.commit()
        else:
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()


def test_outbound_rows_not_dedup_constrained(db, event):
    # Two outbound rows (null raw_message_id) must coexist — SQLite treats NULLs
    # as distinct in a unique constraint.
    for seq in (1, 2):
        db.add(EventMessage(
            event_id=event.id, msg_seq=seq, direction=MessageDirection.OUTBOUND,
            raw_message_id=None, from_callsign="W0NE", to_address="jane@redcross.org",
            subject="reply", body="ack", status=MessageStatus.READ, actor="W0NC",
        ))
    db.commit()
    assert db.query(EventMessage).filter_by(direction=MessageDirection.OUTBOUND).count() == 2


def test_reply_threading_self_fk(db, event):
    raw = _raw(db)
    inbound = EventMessage(
        event_id=event.id, msg_seq=1, direction=MessageDirection.INBOUND,
        raw_message_id=raw.id, from_callsign="KE0XYZ", to_address="W0NE",
        subject="SITREP", body="b",
    )
    db.add(inbound)
    db.commit()
    reply = EventMessage(
        event_id=event.id, msg_seq=2, direction=MessageDirection.OUTBOUND,
        from_callsign="W0NE", to_address="KE0XYZ", subject="Re: SITREP", body="ack",
        status=MessageStatus.READ, actor="W0NC", reply_to_id=inbound.id,
    )
    db.add(reply)
    db.commit()
    db.refresh(reply)
    assert reply.reply_to_id == inbound.id


def test_cascade_delete_with_event(db, event):
    raw = _raw(db)
    db.add(EventMessage(
        event_id=event.id, msg_seq=1, direction=MessageDirection.INBOUND,
        raw_message_id=raw.id, from_callsign="KE0XYZ", to_address="W0NE", subject="s", body="b",
    ))
    db.commit()
    db.delete(event)
    db.commit()
    assert db.query(EventMessage).count() == 0
    # The shared raw message is NOT deleted by cascade.
    assert db.get(RawMessage, raw.id) is not None
