from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.checkins.models import RawMessage, RawMessageAttachment
from backend.modules.checkins.service import scan_and_import_messages
from backend.modules.schedule.models import NetSession, SessionType, SessionStatus
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
def _stub_geocode(monkeypatch):
    from backend.integrations.geocoder import service as geo
    monkeypatch.setattr(geo, "_call_nominatim", lambda *a, **kw: None)


def _session(db, net):
    s = NetSession(
        net_id=net.id, start_date=datetime.now(timezone.utc).date(),
        session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.SCHEDULED,
        grace_period_hours=24,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _msg(mid="M1", atts=None):
    return {
        "message_id": mid, "from_address": "KE0XYZ", "to_address": "W0NE",
        "subject": "s", "body": "KE0XYZ Ben Kuhn", "received_at": datetime.now(timezone.utc),
        "path": None, "attachments": atts or [],
    }


def test_attachments_persisted_on_import(db):
    net = make_test_net(db)
    session = _session(db, net)
    att = {"filename": "form.xml", "content_type": "application/xml", "data": b"<x/>"}
    scan_and_import_messages(db, [_msg(atts=[att])], session, net_id=net.id)
    raw = db.query(RawMessage).filter_by(message_id="M1").one()
    assert len(raw.attachments) == 1
    assert raw.attachments[0].filename == "form.xml"
    assert raw.attachments[0].data == b"<x/>"


def test_attachments_backfilled_on_rescan(db):
    net = make_test_net(db)
    session = _session(db, net)
    # First import with no attachments (simulating a pre-feature import).
    scan_and_import_messages(db, [_msg(atts=[])], session, net_id=net.id)
    assert db.query(RawMessageAttachment).count() == 0
    # Re-scan with attachments present → backfilled.
    att = {"filename": "form.xml", "content_type": "application/xml", "data": b"<x/>"}
    scan_and_import_messages(db, [_msg(atts=[att])], session, net_id=net.id)
    assert db.query(RawMessageAttachment).count() == 1


def test_persist_failure_does_not_block_import(db, monkeypatch):
    # Force RawMessageAttachment construction to raise, simulating a persist failure.
    from backend.modules.checkins import models as m
    from backend.modules.checkins.models import CheckIn

    net = make_test_net(db)
    session = _session(db, net)

    def boom(*a, **kw):
        raise RuntimeError("simulated attachment failure")

    # Patch at the point of use: the helper imports RawMessageAttachment from
    # backend.modules.checkins.models, so patch it there.
    monkeypatch.setattr(m, "RawMessageAttachment", type("Boom", (), {"__init__": boom}))

    att = {"filename": "form.xml", "content_type": "application/xml", "data": b"<x/>"}
    scan_and_import_messages(db, [_msg(atts=[att])], session, net_id=net.id)

    # The check-in imported despite the attachment failure, and the RawMessage row exists.
    raw = db.query(RawMessage).filter_by(message_id="M1").one()
    assert raw is not None
    assert db.query(CheckIn).filter_by(raw_message_id=raw.id).count() == 1
