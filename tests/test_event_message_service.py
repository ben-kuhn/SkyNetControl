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
from backend.modules.events.service import EventNotActiveError, activate_event, close_event, create_event
from backend.modules.events.event_config_service import set_event_config_bulk


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
    # No delivery backends configured → dispatch_delivery returns False but the
    # outbound row is still created (send failure is non-fatal, per spec).
    e = create_event(db, name="E", event_type=EventType.EMERGENCY, created_by="W0NE")
    set_event_config_bulk(db, e.id, {"net_address": "W0NE@winlink.org"})
    activate_event(db, e.id, actor="W0NE")
    db.refresh(e)
    return e


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


class TestDeliveryRouting:
    """Critical 1: outbound send uses winlink backend only, addressed to the composed recipient."""

    def test_sends_to_composed_recipient_not_roster_address(self, db, tmp_path):
        """The .b2f file written to the mailbox out/ dir must be addressed to
        the composed to_address, NOT any roster delivery target_address."""
        from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        e = create_event(db, name="E", event_type=EventType.EMERGENCY, created_by="W0NE")
        set_event_config_bulk(db, e.id, {
            "net_address": "W0NE@winlink.org",
            "pat_mailbox_path": str(tmp_path),
            # event has email configured as a delivery backend,
            # but event messages must override this with winlink-only
            "delivery.backends": '["email"]',
            "delivery.winlink.target_address": "ROSTER@winlink.org",
        })
        activate_event(db, e.id, actor="W0NE")
        db.refresh(e)

        msg = send_event_message(
            db, e.id, actor="W0NC", to_address="jane@redcross.org",
            subject="Status update", body="All clear",
        )

        # Exactly one .b2f file in out/ dir
        b2f_files = list(out_dir.glob("*.b2f"))
        assert len(b2f_files) == 1, "Expected exactly one .b2f file written"

        content = b2f_files[0].read_text()
        # The To: line must be the composed recipient, not the roster address
        assert "jane@redcross.org" in content
        to_lines = [line for line in content.splitlines() if line.startswith("To:")]
        assert len(to_lines) == 1
        assert "jane@redcross.org" in to_lines[0]
        assert "ROSTER@winlink.org" not in to_lines[0]

        # Delivery log must show winlink as the backend used
        logs = db.query(DeliveryLog).filter_by(content_type="event_message", content_id=msg.id).all()
        assert len(logs) == 1
        assert logs[0].backend == "winlink"
        assert logs[0].status == DeliveryStatus.SENT

    def test_email_backend_not_invoked_even_when_configured(self, db, tmp_path):
        """Even if delivery.backends includes email, event messages
        go to winlink only (backends override)."""
        from unittest.mock import patch, MagicMock
        from backend.integrations.delivery.backends.base import DeliveryResult
        from backend.integrations.delivery.models import DeliveryLog

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        e = create_event(db, name="E2", event_type=EventType.EMERGENCY, created_by="W0NE")
        set_event_config_bulk(db, e.id, {
            "net_address": "W0NE@winlink.org",
            "pat_mailbox_path": str(tmp_path),
            "delivery.backends": '["email"]',
            "delivery.email.to_address": "net@example.com",
        })
        activate_event(db, e.id, actor="W0NE")
        db.refresh(e)

        email_send_called = []

        def mock_get_backend(name):
            from backend.integrations.delivery.backends import get_backend as _real
            if name == "email":
                m = MagicMock()
                m.send.side_effect = lambda *a, **kw: email_send_called.append(True) or DeliveryResult(success=True, error=None)
                return m
            return _real(name)

        with patch("backend.integrations.delivery.service.get_backend", side_effect=mock_get_backend):
            send_event_message(
                db, e.id, actor="W0NC", to_address="jane@redcross.org",
                subject="Status", body="all clear",
            )

        assert not email_send_called, "Email backend must NOT be invoked for event messages"
        # But the winlink .b2f must still be written (real backend ran)
        assert list((tmp_path / "out").glob("*.b2f")), "Winlink .b2f must be written"
