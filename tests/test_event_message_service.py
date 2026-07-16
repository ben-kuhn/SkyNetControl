import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import EventMessage, EventStatus, EventType, MessageDirection, MessageStatus
from backend.modules.events.message_service import (
    send_event_message,
    set_message_status,
    validate_to_address,
)
from backend.modules.events.service import EventNotActiveError, close_event, create_event
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


@pytest.fixture
def event(db):
    net = make_test_net(db)
    # No delivery backends configured → dispatch_delivery returns False but the
    # outbound row is still created (send failure is non-fatal, per spec).
    set_net_config_bulk(db, net.id, {"net_address": "W0NE@winlink.org"})
    return create_event(db, net_id=net.id, name="E", event_type=EventType.EMERGENCY,
                        created_by="W0NE", activate=True)


class TestValidateAddress:
    def test_accepts_callsign(self):
        assert validate_to_address(" ke0xyz ") == "ke0xyz"

    def test_accepts_winlink_and_email(self):
        assert validate_to_address("KE0XYZ@winlink.org") == "KE0XYZ@winlink.org"
        assert validate_to_address("jane@redcross.org") == "jane@redcross.org"

    def test_strips_crlf(self):
        assert "\n" not in validate_to_address("a@b.org\r\nCc: evil@x")
        assert "\r" not in validate_to_address("a@b.org\r\nCc: evil@x")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            validate_to_address("   ")

    def test_rejects_oversized(self):
        with pytest.raises(ValueError):
            validate_to_address("x" * 256 + "@y.org")


class TestSend:
    def test_creates_outbound_row(self, db, event):
        msg = send_event_message(
            db, event.id, actor="W0NC", to_address="jane@redcross.org",
            subject="Status", body="all clear",
        )
        assert msg.direction == MessageDirection.OUTBOUND
        assert msg.status == MessageStatus.READ
        assert msg.actor == "W0NC"
        assert msg.from_callsign == "W0NE"  # net callsign, derived from net_address
        assert msg.msg_seq == 1

    def test_reply_links_parent(self, db, event):
        from backend.modules.events.models import EventMessage as EM
        inbound = EM(
            event_id=event.id, msg_seq=99, direction=MessageDirection.INBOUND,
            from_callsign="KE0XYZ", to_address="W0NE", subject="SITREP", body="x",
        )
        db.add(inbound)
        db.commit()
        reply = send_event_message(
            db, event.id, actor="W0NC", to_address="KE0XYZ", subject="Re: SITREP",
            body="ack", reply_to_id=inbound.id,
        )
        assert reply.reply_to_id == inbound.id

    def test_send_on_closed_event_raises(self, db, event):
        close_event(db, event.id, actor="W0NE")
        with pytest.raises(EventNotActiveError):
            send_event_message(db, event.id, actor="W0NC", to_address="a@b.org",
                               subject="s", body="b")

    def test_bad_address_raises(self, db, event):
        with pytest.raises(ValueError):
            send_event_message(db, event.id, actor="W0NC", to_address="  ", subject="s", body="b")


class TestStatus:
    def test_mark_read_and_dismissed(self, db, event):
        from backend.modules.events.models import EventMessage as EM
        m = EM(event_id=event.id, msg_seq=1, direction=MessageDirection.INBOUND,
               from_callsign="KE0XYZ", to_address="W0NE", subject="s", body="b")
        db.add(m)
        db.commit()
        assert set_message_status(db, event.id, m.id, MessageStatus.READ).status == MessageStatus.READ
        assert set_message_status(db, event.id, m.id, MessageStatus.DISMISSED).status == MessageStatus.DISMISSED

    def test_missing_returns_none(self, db, event):
        assert set_message_status(db, event.id, 9999, MessageStatus.READ) is None
