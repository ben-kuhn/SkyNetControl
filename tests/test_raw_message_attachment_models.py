from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.checkins.models import MessageType, RawMessage, RawMessageAttachment


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _raw(db):
    raw = RawMessage(
        message_id="M1", from_address="KE0XYZ", received_at=datetime.now(timezone.utc),
        subject="s", body="b", message_type=MessageType.UNKNOWN, parsed=False,
    )
    db.add(raw)
    db.commit()
    db.refresh(raw)
    return raw


def test_attachment_persists_binary(db):
    raw = _raw(db)
    att = RawMessageAttachment(
        raw_message_id=raw.id, filename="form.xml",
        content_type="application/xml", data=b"<x/>\xff",
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    assert att.data == b"<x/>\xff"
    assert att.created_at is not None


def test_attachments_relationship(db):
    raw = _raw(db)
    db.add(RawMessageAttachment(raw_message_id=raw.id, filename="a.xml", content_type="application/xml", data=b"a"))
    db.add(RawMessageAttachment(raw_message_id=raw.id, filename="b.jpg", content_type="image/jpeg", data=b"b"))
    db.commit()
    db.refresh(raw)
    assert len(raw.attachments) == 2


def test_cascade_delete_with_raw_message(db):
    raw = _raw(db)
    db.add(RawMessageAttachment(raw_message_id=raw.id, filename="a.xml", content_type="application/xml", data=b"a"))
    db.commit()
    db.delete(raw)
    db.commit()
    assert db.query(RawMessageAttachment).count() == 0
