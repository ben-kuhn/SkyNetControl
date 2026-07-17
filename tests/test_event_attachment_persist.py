"""Tests for Critical 1 fix: _persist_raw_messages must persist attachments.

The event-path scanner (_persist_raw_messages) is called when an event is
active but no check-in session window is open. Before the fix it created
RawMessage rows but dropped all attachments. These tests verify the fix.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.integrations.scanner.service import _persist_raw_messages
from backend.modules.checkins.models import RawMessage, RawMessageAttachment


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _msg(mid="M1", atts=None):
    return {
        "message_id": mid,
        "from_address": "KE0XYZ@winlink.org",
        "to_address": "W0NE@winlink.org",
        "subject": "SITREP",
        "body": "all clear",
        "received_at": datetime.now(timezone.utc),
        "path": None,
        "attachments": atts or [],
    }


def test_persist_raw_messages_creates_attachment_rows(db):
    """New RawMessage via _persist_raw_messages must also persist its attachments."""
    att = {"filename": "RMS_Express_Form_ICS213.xml", "content_type": "application/xml", "data": b"<form/>"}
    _persist_raw_messages(db, [_msg(atts=[att])])

    raw = db.query(RawMessage).filter_by(message_id="M1").one()
    assert raw is not None
    atts = db.query(RawMessageAttachment).filter_by(raw_message_id=raw.id).all()
    assert len(atts) == 1
    assert atts[0].filename == "RMS_Express_Form_ICS213.xml"
    assert atts[0].data == b"<form/>"


def test_persist_raw_messages_multiple_attachments(db):
    """Multiple attachments on a single message are all persisted."""
    atts = [
        {"filename": "form.xml", "content_type": "application/xml", "data": b"<f/>"},
        {"filename": "photo.jpg", "content_type": "image/jpeg", "data": b"\xff\xd8\xff"},
    ]
    _persist_raw_messages(db, [_msg(atts=atts)])

    raw = db.query(RawMessage).filter_by(message_id="M1").one()
    count = db.query(RawMessageAttachment).filter_by(raw_message_id=raw.id).count()
    assert count == 2


def test_persist_raw_messages_backfills_existing_row(db):
    """If a RawMessage already exists with no attachments, _persist_raw_messages
    must backfill the attachments on the re-run (backfill branch)."""
    # First call: no attachments
    _persist_raw_messages(db, [_msg()])
    raw = db.query(RawMessage).filter_by(message_id="M1").one()
    assert db.query(RawMessageAttachment).filter_by(raw_message_id=raw.id).count() == 0

    # Second call: same message_id, now with an attachment
    att = {"filename": "form.xml", "content_type": "application/xml", "data": b"<x/>"}
    _persist_raw_messages(db, [_msg(atts=[att])])

    # Attachment must now be present (backfilled)
    count = db.query(RawMessageAttachment).filter_by(raw_message_id=raw.id).count()
    assert count == 1
    assert db.query(RawMessageAttachment).filter_by(raw_message_id=raw.id).one().data == b"<x/>"


def test_persist_raw_messages_no_attachments_no_rows(db):
    """A message with no attachments must not create any RawMessageAttachment rows."""
    _persist_raw_messages(db, [_msg()])
    assert db.query(RawMessageAttachment).count() == 0


def test_persist_raw_messages_dedup_skips_duplicate_message_id(db):
    """Calling _persist_raw_messages twice with the same message_id must not
    create a second RawMessage row."""
    att = {"filename": "f.xml", "content_type": "application/xml", "data": b"<a/>"}
    _persist_raw_messages(db, [_msg(atts=[att])])
    _persist_raw_messages(db, [_msg(atts=[att])])

    assert db.query(RawMessage).filter_by(message_id="M1").count() == 1
