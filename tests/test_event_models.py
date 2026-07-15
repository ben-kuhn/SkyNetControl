import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import (
    Event,
    EventLogEntry,
    EventLogType,
    EventParticipant,
    EventPost,
    EventStatus,
    EventType,
    ParticipantStatus,
)
from tests.conftest import make_test_net


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def net(db):
    return make_test_net(db)


def make_event(db, net, **overrides):
    event = Event(
        net_id=net.id,
        name=overrides.get("name", "Field Day"),
        event_type=overrides.get("event_type", EventType.PUBLIC_SERVICE),
        created_by=overrides.get("created_by", "W0NE"),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def test_event_defaults(db, net):
    event = make_event(db, net)
    assert event.status == EventStatus.DRAFT
    assert event.log_seq == 0
    assert event.activated_at is None
    assert event.closed_at is None
    assert event.created_at is not None


def test_post_unique_name_per_event(db, net):
    event = make_event(db, net)
    db.add(EventPost(event_id=event.id, name="Rest Stop 3"))
    db.commit()
    db.add(EventPost(event_id=event.id, name="Rest Stop 3"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # Same name on a different event is fine
    other = make_event(db, net, name="Other")
    db.add(EventPost(event_id=other.id, name="Rest Stop 3"))
    db.commit()


def test_participant_unique_callsign_per_event(db, net):
    event = make_event(db, net)
    db.add(EventParticipant(event_id=event.id, callsign="KE0XYZ"))
    db.commit()
    db.add(EventParticipant(event_id=event.id, callsign="KE0XYZ"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_participant_defaults(db, net):
    event = make_event(db, net)
    p = EventParticipant(event_id=event.id, callsign="KE0XYZ")
    db.add(p)
    db.commit()
    db.refresh(p)
    assert p.current_status == ParticipantStatus.CHECKED_IN
    assert p.checked_in_at is not None
    assert p.checked_out_at is None
    assert p.post_id is None


def test_log_entry_unique_seq_per_event(db, net):
    event = make_event(db, net)
    db.add(EventLogEntry(
        event_id=event.id, seq=1, entry_type=EventLogType.SYSTEM,
        actor="W0NE", message="Event activated",
    ))
    db.commit()
    db.add(EventLogEntry(
        event_id=event.id, seq=1, entry_type=EventLogType.NOTE,
        actor="W0NE", message="dup seq",
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_log_entry_defaults(db, net):
    event = make_event(db, net)
    entry = EventLogEntry(
        event_id=event.id, seq=1, entry_type=EventLogType.SYSTEM,
        actor="W0NE", message="KE0XYZ checked in", callsign="KE0XYZ",
        new_status=ParticipantStatus.CHECKED_IN,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    assert entry.pinned is False
    assert entry.created_at is not None


def test_event_cascade_delete(db, net):
    event = make_event(db, net)
    db.add(EventPost(event_id=event.id, name="EOC"))
    db.add(EventParticipant(event_id=event.id, callsign="KE0XYZ"))
    db.add(EventLogEntry(
        event_id=event.id, seq=1, entry_type=EventLogType.SYSTEM,
        actor="W0NE", message="x",
    ))
    db.commit()
    db.delete(event)
    db.commit()
    assert db.query(EventPost).count() == 0
    assert db.query(EventParticipant).count() == 0
    assert db.query(EventLogEntry).count() == 0
