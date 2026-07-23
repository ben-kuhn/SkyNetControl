"""Tests for event form compose / send / reply-form — service-level.

Converted from HTTP-layer tests (Task 7): routes.py still has net_id
references that are fixed in Task 11. These tests exercise message_service
and the form builder directly, bypassing the (broken) event HTTP routes.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import (
    Event, EventMessage, EventMessageForm, EventStatus, EventType,
    MessageDirection, MessageStatus,
)
from backend.modules.events.event_config_service import set_event_config
from backend.modules.events.message_service import (
    compose_form_preview, send_event_form_message, validate_to_address,
)
from backend.modules.events.service import activate_event, close_event, create_event


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def forms_dir(tmp_path, monkeypatch):
    """A forms library with one composable template."""
    forms = tmp_path / "forms"
    (forms / "ICS USA").mkdir(parents=True)
    (forms / "ICS USA" / "ICS213.txt").write_text(
        "Form: ICS213Input.html\nTo: <Var ToStation>\nSubject: <Var Subject>\nMsg:\n<Var MsgBody>\n"
    )
    (forms / "ICS USA" / "ICS213Input.html").write_text("<form><input name='MsgBody'></form>")
    import backend.modules.forms.builder as bld
    monkeypatch.setattr(bld, "forms_library_dir", lambda: forms)
    return forms


@pytest.fixture
def active_event(db):
    event = create_event(db, name="E", event_type=EventType.EMERGENCY, created_by="W0NC")
    event = activate_event(db, event.id, actor="W0NC")
    # Set event config so from_callsign resolves from event config
    set_event_config(db, event.id, "net_address", "W0NE@winlink.org")
    return event


def _noop_dispatch(db, content_type, content_id, subject, body, net_id=None, *,
                   event_id=None, backends=None, config_overrides=None):
    return True


class TestPreview:
    def test_preview_builds_without_send(self, db, active_event, forms_dir, monkeypatch):
        import backend.integrations.delivery.service as ds
        monkeypatch.setattr(ds, "dispatch_delivery", _noop_dispatch)
        result = compose_form_preview(
            db, active_event.id,
            template_path="ICS USA/ICS213.txt",
            variables={"ToStation": "KE0XYZ", "Subject": "SITREP", "MsgBody": "all clear"},
            datetime_stamp="2026/07/17 18:30",
        )
        assert result["to"] == "KE0XYZ"
        assert result["subject"] == "SITREP"
        assert "all clear" in result["body"]
        assert result["attachment_filename"].startswith("RMS_Express_Form_")


class TestSend:
    def test_send_creates_message_and_form_record(self, db, active_event, forms_dir, monkeypatch):
        import backend.integrations.delivery.service as ds
        monkeypatch.setattr(ds, "dispatch_delivery", _noop_dispatch)
        msg = send_event_form_message(
            db, active_event.id,
            actor="W0NC",
            template_path="ICS USA/ICS213.txt",
            variables={"ToStation": "KE0XYZ", "Subject": "S", "MsgBody": "x"},
            datetime_stamp="2026/07/17 18:30",
        )
        assert msg.direction == MessageDirection.OUTBOUND
        assert msg.to_address == "KE0XYZ"
        rec = db.query(EventMessageForm).one()
        assert rec.template_path == "ICS USA/ICS213.txt"
        assert rec.variables["MsgBody"] == "x"

    def test_send_closed_event_409(self, db, active_event, forms_dir, monkeypatch):
        import backend.integrations.delivery.service as ds
        monkeypatch.setattr(ds, "dispatch_delivery", _noop_dispatch)
        close_event(db, active_event.id, actor="W0NC")
        from backend.modules.events.service import EventNotActiveError
        with pytest.raises(EventNotActiveError):
            send_event_form_message(
                db, active_event.id, actor="W0NC",
                template_path="ICS USA/ICS213.txt", variables={}, datetime_stamp="2026/07/17 18:30",
            )

    def test_bad_template_422(self, db, active_event, forms_dir, monkeypatch):
        import backend.integrations.delivery.service as ds
        monkeypatch.setattr(ds, "dispatch_delivery", _noop_dispatch)
        with pytest.raises(ValueError):
            send_event_form_message(
                db, active_event.id, actor="W0NC",
                template_path="ICS USA/Nope.txt", variables={}, datetime_stamp="2026/07/17 18:30",
            )

    def test_empty_to_send_422(self, db, active_event, forms_dir, monkeypatch):
        """A form whose To: resolves to empty (no ToStation variable) must raise ValueError."""
        import backend.integrations.delivery.service as ds
        monkeypatch.setattr(ds, "dispatch_delivery", _noop_dispatch)
        with pytest.raises(ValueError, match="no destination address"):
            send_event_form_message(
                db, active_event.id, actor="W0NC",
                template_path="ICS USA/ICS213.txt",
                variables={"Subject": "test", "MsgBody": "hello"},
                datetime_stamp="2026/07/17 18:30",
            )

    def test_empty_to_preview_422(self, db, active_event, forms_dir):
        """Preview with empty To: must raise ValueError."""
        with pytest.raises(ValueError, match="no destination address"):
            compose_form_preview(
                db, active_event.id,
                template_path="ICS USA/ICS213.txt",
                variables={"Subject": "test", "MsgBody": "hello"},
                datetime_stamp="2026/07/17 18:30",
            )


# XML carrying a reply_template that points at ICS213.txt (already in the fixture's forms dir)
_REPLY_FORM_XML = (
    "<RMS_Express_Form>"
    "<form_parameters>"
    "<display_form>ICS213Input.html</display_form>"
    "<reply_template>ICS213.txt</reply_template>"
    "</form_parameters>"
    "<variables><msgbody>all clear</msgbody><tostation>W0NC</tostation></variables>"
    "</RMS_Express_Form>"
)


class TestReplyForm:
    """Tests for resolve_reply_form."""

    def _seed_inbound_with_form(self, db, event_id):
        from datetime import datetime, timezone
        from backend.modules.checkins.models import MessageType, RawMessage, RawMessageAttachment

        raw = RawMessage(
            message_id="RF1",
            from_address="KE0XYZ@winlink.org",
            received_at=datetime.now(timezone.utc),
            subject="ICS213 Report",
            body="see form",
            message_type=MessageType.WINLINK_FORM,
            parsed=True,
        )
        db.add(raw)
        db.flush()
        att = RawMessageAttachment(
            raw_message_id=raw.id,
            filename="RMS_Express_Form_ICS213.xml",
            content_type="application/xml",
            data=_REPLY_FORM_XML.encode("utf-8"),
        )
        db.add(att)
        msg = EventMessage(
            event_id=event_id,
            msg_seq=99,
            direction=MessageDirection.INBOUND,
            raw_message_id=raw.id,
            from_callsign="KE0XYZ",
            to_address="W0NE@winlink.org",
            subject="ICS213 Report",
            body="see form",
            status=MessageStatus.UNREAD,
        )
        db.add(msg)
        db.commit()
        return msg.id

    def _seed_plain_inbound(self, db, event_id):
        from datetime import datetime, timezone
        from backend.modules.checkins.models import MessageType, RawMessage

        raw = RawMessage(
            message_id="PL1",
            from_address="KE0ABC@winlink.org",
            received_at=datetime.now(timezone.utc),
            subject="No form here",
            body="just text",
            message_type=MessageType.PLAIN_TEXT,
            parsed=True,
        )
        db.add(raw)
        db.flush()
        msg = EventMessage(
            event_id=event_id,
            msg_seq=100,
            direction=MessageDirection.INBOUND,
            raw_message_id=raw.id,
            from_callsign="KE0ABC",
            to_address="W0NE@winlink.org",
            subject="No form here",
            body="just text",
            status=MessageStatus.UNREAD,
        )
        db.add(msg)
        db.commit()
        return msg.id

    def test_reply_form_404_plain_message(self, db, active_event):
        from backend.modules.events.message_service import resolve_reply_form
        mid = self._seed_plain_inbound(db, active_event.id)
        with pytest.raises(ValueError):
            resolve_reply_form(db, active_event.id, mid)

    def test_reply_form_malformed_xml_returns_404(self, db, active_event):
        from datetime import datetime, timezone
        from backend.modules.checkins.models import MessageType, RawMessage, RawMessageAttachment
        from backend.modules.events.message_service import resolve_reply_form

        malformed_xml = "<RMS_Express_Form><bad></bad attr></RMS_Express_Form>"

        raw = RawMessage(
            message_id="MAL1",
            from_address="KE0XYZ@winlink.org",
            received_at=datetime.now(timezone.utc),
            subject="Malformed form",
            body="see form",
            message_type=MessageType.WINLINK_FORM,
            parsed=True,
        )
        db.add(raw)
        db.flush()
        att = RawMessageAttachment(
            raw_message_id=raw.id,
            filename="RMS_Express_Form_Bad.xml",
            content_type="application/xml",
            data=malformed_xml.encode("utf-8"),
        )
        db.add(att)
        msg = EventMessage(
            event_id=active_event.id,
            msg_seq=88,
            direction=MessageDirection.INBOUND,
            raw_message_id=raw.id,
            from_callsign="KE0XYZ",
            to_address="W0NE@winlink.org",
            subject="Malformed form",
            body="see form",
            status=MessageStatus.UNREAD,
        )
        db.add(msg)
        db.commit()
        with pytest.raises(ValueError):
            resolve_reply_form(db, active_event.id, msg.id)

    def test_reply_form_200_happy_path(self, db, active_event, forms_dir, monkeypatch):
        import backend.modules.forms.library as lib
        from backend.modules.events.message_service import resolve_reply_form

        monkeypatch.setattr(lib, "forms_library_dir", lambda: forms_dir)
        lib.clear_template_cache()

        mid = self._seed_inbound_with_form(db, active_event.id)
        result = resolve_reply_form(db, active_event.id, mid)
        assert result["reply_template_path"] == "ICS USA/ICS213.txt"
        assert result["input_form_path"] == "ICS USA/ICS213Input.html"
        assert result["prefill"]["msgbody"] == "all clear"
        assert result["prefill"]["tostation"] == "W0NC"
