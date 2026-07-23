import secrets
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import (
    Event, EventMessage, EventMessageForm, EventType, MessageDirection, MessageStatus,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


def _msg(db):
    event = Event(name="E", event_type=EventType.EMERGENCY, created_by="W0NE",
                  public_token=secrets.token_urlsafe(16))
    db.add(event); db.commit(); db.refresh(event)
    m = EventMessage(event_id=event.id, msg_seq=1, direction=MessageDirection.OUTBOUND,
                     from_callsign="W0NE", to_address="KE0XYZ", subject="s", body="b",
                     status=MessageStatus.READ, actor="W0NC")
    db.add(m); db.commit(); db.refresh(m)
    return m


def test_form_record_persists(db):
    m = _msg(db)
    rec = EventMessageForm(event_message_id=m.id, template_path="ICS USA/ICS213.txt",
                           display_form="ICS213Input.html", reply_template=None,
                           variables={"MsgBody": "x", "ToStation": "KE0XYZ"},
                           datetime_stamp="2026/07/17 18:30")
    db.add(rec); db.commit(); db.refresh(rec)
    assert rec.variables["MsgBody"] == "x"
    assert rec.created_at is not None


def test_cascade_delete_with_message(db):
    m = _msg(db)
    db.add(EventMessageForm(event_message_id=m.id, template_path="t", display_form="d",
                            variables={}, datetime_stamp="2026/07/17 18:30"))
    db.commit()
    db.delete(m); db.commit()
    assert db.query(EventMessageForm).count() == 0
