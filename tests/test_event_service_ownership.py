# tests/test_event_service_ownership.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import EventType
from backend.modules.events import service


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_create_event_sets_owner_and_token():
    db = _db()
    ev = service.create_event(db, name="Skywarn", event_type=EventType.EMERGENCY, created_by="W0NC")
    assert ev.created_by == "W0NC"
    assert ev.visibility == "private"
    assert ev.public_token and len(ev.public_token) >= 16
    assert not hasattr(ev, "net_id") or "net_id" not in {c.name for c in ev.__table__.columns}


def test_operator_add_remove_and_transfer():
    db = _db()
    ev = service.create_event(db, name="E", event_type=EventType.EMERGENCY, created_by="W0NC")
    service.add_operator(db, ev, "KD0OP", added_by="W0NC")
    assert service.list_operators(db, ev) == ["KD0OP"]
    service.remove_operator(db, ev, "KD0OP")
    assert service.list_operators(db, ev) == []
    service.transfer_owner(db, ev, "KE0NEW")
    assert ev.created_by == "KE0NEW"


def test_rotate_token_changes_it():
    db = _db()
    ev = service.create_event(db, name="E", event_type=EventType.EMERGENCY, created_by="W0NC")
    old = ev.public_token
    new = service.rotate_public_token(db, ev)
    assert new != old and ev.public_token == new
