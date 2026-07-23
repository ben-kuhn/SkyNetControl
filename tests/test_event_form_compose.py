"""Tests for event form compose / send / reply-form.

Service-level tests exercise message_service and the form builder directly.
HTTP-layer tests (class TestHttpFormRoutes) exercise the real FastAPI routes
at /api/events/{id}/forms/preview, /api/events/{id}/form-messages, and
/api/events/{id}/messages/{id}/reply-form, and assert the two invariants:
  (a) msg.from_callsign == event-config-derived callsign (creator/net_address)
  (b) the send path dispatches with event_id= (queues a delivery for the event)
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


# ---------------------------------------------------------------------------
# HTTP-layer tests — restored in Task 11 with two invariant assertions
# ---------------------------------------------------------------------------

@pytest.fixture
def http_db_setup(tmp_path):
    """In-memory DB + forms dir for HTTP-layer form tests."""
    forms = tmp_path / "forms"
    (forms / "ICS USA").mkdir(parents=True)
    (forms / "ICS USA" / "ICS213.txt").write_text(
        "Form: ICS213Input.html\nTo: <Var ToStation>\nSubject: <Var Subject>\nMsg:\n<Var MsgBody>\n"
    )
    (forms / "ICS USA" / "ICS213Input.html").write_text("<form><input name='MsgBody'></form>")

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    from backend.auth.models import User
    from backend.modules.events.event_config_service import set_event_config

    with factory() as session:
        session.add(User(callsign="W0NC", oidc_subject="auth0|nc", name="NC"))
        session.commit()

    return {"engine": engine, "factory": factory, "forms": forms}


@pytest.fixture
def http_app(http_db_setup):
    from backend.config import Settings
    from backend.app import create_app
    application = create_app(settings=Settings(
        database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60
    ))
    application.state.engine = http_db_setup["engine"]
    application.state.session_factory = http_db_setup["factory"]
    return application


@pytest.fixture
async def http_nc_client(http_app):
    from httpx import ASGITransport, AsyncClient
    from backend.config import Settings
    from tests.conftest import make_test_token
    token = make_test_token("W0NC", Settings(
        database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60
    ))
    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token}) as c:
        yield c


@pytest.fixture
async def http_active_event(http_nc_client, http_db_setup):
    from backend.modules.events.event_config_service import set_event_config
    resp = await http_nc_client.post("/api/events", json={"name": "E", "event_type": "emergency"})
    assert resp.status_code == 201
    event_id = resp.json()["id"]
    resp2 = await http_nc_client.post(f"/api/events/{event_id}/activate")
    assert resp2.status_code == 200
    # Set net_address so from_callsign derives from event config
    with http_db_setup["factory"]() as db:
        set_event_config(db, event_id, "net_address", "W0NE@winlink.org")
    return event_id


class TestHttpFormRoutes:
    """HTTP-layer form route tests. Assert routes exist and both invariants hold."""

    async def test_preview_route_200(self, http_nc_client, http_active_event, http_db_setup, monkeypatch):
        import backend.modules.forms.builder as bld
        monkeypatch.setattr(bld, "forms_library_dir", lambda: http_db_setup["forms"])
        resp = await http_nc_client.post(
            f"/api/events/{http_active_event}/forms/preview",
            json={
                "template_path": "ICS USA/ICS213.txt",
                "variables": {"ToStation": "KE0XYZ", "Subject": "SITREP", "MsgBody": "all clear"},
                "datetime_stamp": "2026/07/17 18:30",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["to"] == "KE0XYZ"
        assert data["subject"] == "SITREP"

    async def test_form_send_route_201_and_invariants(
        self, http_nc_client, http_active_event, http_db_setup, monkeypatch
    ):
        """Invariant (a): from_callsign == event-config callsign (W0NE from net_address).
        Invariant (b): dispatch called with event_id= (delivery queued for the event)."""
        import backend.modules.forms.builder as bld
        import backend.integrations.delivery.service as ds

        monkeypatch.setattr(bld, "forms_library_dir", lambda: http_db_setup["forms"])

        dispatch_calls = []

        def capturing_dispatch(db, content_type, content_id, subject, body, net_id=None, *,
                               event_id=None, backends=None, config_overrides=None):
            dispatch_calls.append({
                "content_type": content_type, "content_id": content_id,
                "net_id": net_id, "event_id": event_id,
            })
            return True

        monkeypatch.setattr(ds, "dispatch_delivery", capturing_dispatch)

        resp = await http_nc_client.post(
            f"/api/events/{http_active_event}/form-messages",
            json={
                "template_path": "ICS USA/ICS213.txt",
                "variables": {"ToStation": "KE0XYZ", "Subject": "SITREP", "MsgBody": "all clear"},
                "datetime_stamp": "2026/07/17 18:30",
            },
        )
        assert resp.status_code == 201
        msg = resp.json()["message"]

        # Invariant (a): from_callsign derives from event config net_address
        assert msg["from_callsign"] == "W0NE", (
            f"Expected from_callsign='W0NE' (from event net_address W0NE@winlink.org), got {msg['from_callsign']!r}"
        )

        # Invariant (b): dispatch called with event_id=, not net_id
        assert len(dispatch_calls) == 1
        call = dispatch_calls[0]
        assert call["event_id"] == http_active_event, (
            f"Expected dispatch with event_id={http_active_event}, got event_id={call['event_id']!r}"
        )
        assert call["net_id"] is None, (
            f"Expected dispatch with net_id=None, got net_id={call['net_id']!r}"
        )
        assert call["content_type"] == "event_message"

    async def test_reply_form_route_200(
        self, http_nc_client, http_active_event, http_db_setup, monkeypatch
    ):
        """Reply-form route resolves the form data from an inbound form message."""
        from datetime import datetime, timezone
        from backend.modules.checkins.models import MessageType, RawMessage, RawMessageAttachment
        from backend.modules.events.models import EventMessage, MessageDirection, MessageStatus
        import backend.modules.forms.library as lib

        monkeypatch.setattr(lib, "forms_library_dir", lambda: http_db_setup["forms"])
        lib.clear_template_cache()

        reply_xml = (
            "<RMS_Express_Form>"
            "<form_parameters>"
            "<display_form>ICS213Input.html</display_form>"
            "<reply_template>ICS213.txt</reply_template>"
            "</form_parameters>"
            "<variables><msgbody>all clear</msgbody><tostation>W0NC</tostation></variables>"
            "</RMS_Express_Form>"
        )

        with http_db_setup["factory"]() as db:
            raw = RawMessage(
                message_id="RF1", from_address="KE0XYZ@winlink.org",
                received_at=datetime.now(timezone.utc),
                subject="ICS213", body="see form",
                message_type=MessageType.WINLINK_FORM, parsed=True,
            )
            db.add(raw)
            db.flush()
            db.add(RawMessageAttachment(
                raw_message_id=raw.id, filename="RMS_Express_Form_ICS213.xml",
                content_type="application/xml", data=reply_xml.encode(),
            ))
            msg = EventMessage(
                event_id=http_active_event, msg_seq=99,
                direction=MessageDirection.INBOUND,
                raw_message_id=raw.id, from_callsign="KE0XYZ",
                to_address="W0NE@winlink.org", subject="ICS213",
                body="see form", status=MessageStatus.UNREAD,
            )
            db.add(msg)
            db.commit()
            msg_id = msg.id

        resp = await http_nc_client.get(f"/api/events/{http_active_event}/messages/{msg_id}/reply-form")
        assert resp.status_code == 200
        data = resp.json()
        assert data["reply_template_path"] == "ICS USA/ICS213.txt"
        assert data["prefill"]["msgbody"] == "all clear"

    async def test_form_preview_missing_to_422(
        self, http_nc_client, http_active_event, http_db_setup, monkeypatch
    ):
        import backend.modules.forms.builder as bld
        monkeypatch.setattr(bld, "forms_library_dir", lambda: http_db_setup["forms"])
        resp = await http_nc_client.post(
            f"/api/events/{http_active_event}/forms/preview",
            json={
                "template_path": "ICS USA/ICS213.txt",
                "variables": {"Subject": "SITREP", "MsgBody": "all clear"},
                "datetime_stamp": "2026/07/17 18:30",
            },
        )
        assert resp.status_code == 422
