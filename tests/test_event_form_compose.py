import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.events.models import EventMessage, EventMessageForm, MessageDirection, MessageStatus
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.modules.nets.config_service import set_net_config_bulk
from tests.conftest import make_test_token

NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/events"


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)


@pytest.fixture
def db_setup(tmp_path, monkeypatch):
    # A forms library with one composable template, patched into builder + serve.
    forms = tmp_path / "forms"
    (forms / "ICS USA").mkdir(parents=True)
    (forms / "ICS USA" / "ICS213.txt").write_text(
        "Form: ICS213Input.html\nTo: <Var ToStation>\nSubject: <Var Subject>\nMsg:\n<Var MsgBody>\n"
    )
    (forms / "ICS USA" / "ICS213Input.html").write_text("<form><input name='MsgBody'></form>")
    import backend.modules.forms.builder as bld
    monkeypatch.setattr(bld, "forms_library_dir", lambda: forms)

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        nc = User(callsign="W0NC", oidc_subject="auth0|nc", name="NC")
        net = Net(slug=NET_SLUG, name="Test Net", is_public=False)
        s.add_all([nc, net]); s.flush()
        s.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        set_net_config_bulk(s, net.id, {"net_address": "W0NE@winlink.org",
                                        "pat_mailbox_path": str(tmp_path / "mailbox")})
        s.commit()
        yield {"engine": engine, "factory": factory, "forms": forms}
    engine.dispose()


@pytest.fixture
def app(test_settings, db_setup):
    from backend.app import create_app
    a = create_app(settings=test_settings)
    a.state.engine = db_setup["engine"]; a.state.session_factory = db_setup["factory"]
    return a


@pytest.fixture
async def nc(app, test_settings):
    token = make_test_token("W0NC", test_settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           cookies={"access_token": token}) as c:
        yield c


@pytest.fixture
async def active_event(nc):
    return (await nc.post(BASE, json={"name": "E", "event_type": "emergency", "activate": True})).json()["id"]


class TestPreview:
    async def test_preview_builds_without_send(self, nc, active_event):
        resp = await nc.post(f"{BASE}/{active_event}/forms/preview", json={
            "template_path": "ICS USA/ICS213.txt",
            "variables": {"ToStation": "KE0XYZ", "Subject": "SITREP", "MsgBody": "all clear"},
            "datetime_stamp": "2026/07/17 18:30",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["to"] == "KE0XYZ"
        assert body["subject"] == "SITREP"
        assert "all clear" in body["body"]
        assert body["attachment_filename"].startswith("RMS_Express_Form_")


class TestSend:
    async def test_send_creates_message_and_form_record(self, nc, active_event, db_setup):
        resp = await nc.post(f"{BASE}/{active_event}/form-messages", json={
            "template_path": "ICS USA/ICS213.txt",
            "variables": {"ToStation": "KE0XYZ", "Subject": "S", "MsgBody": "x"},
            "datetime_stamp": "2026/07/17 18:30",
        })
        assert resp.status_code == 201
        msg = resp.json()["message"]
        assert msg["direction"] == "outbound"
        assert msg["to_address"] == "KE0XYZ"
        with db_setup["factory"]() as db:
            rec = db.query(EventMessageForm).one()
            assert rec.template_path == "ICS USA/ICS213.txt"
            assert rec.variables["MsgBody"] == "x"

    async def test_send_closed_event_409(self, nc, active_event):
        await nc.post(f"{BASE}/{active_event}/close")
        resp = await nc.post(f"{BASE}/{active_event}/form-messages", json={
            "template_path": "ICS USA/ICS213.txt", "variables": {}, "datetime_stamp": "2026/07/17 18:30"})
        assert resp.status_code == 409

    async def test_bad_template_422(self, nc, active_event):
        resp = await nc.post(f"{BASE}/{active_event}/form-messages", json={
            "template_path": "ICS USA/Nope.txt", "variables": {}, "datetime_stamp": "2026/07/17 18:30"})
        assert resp.status_code == 422

    async def test_empty_to_send_422(self, nc, active_event, db_setup):
        """A form whose To: resolves to empty (no ToStation variable provided)
        must return 422, not persist a corrupt message or silently fail in PAT."""
        # ICS213.txt has "To: <Var ToStation>"; omitting ToStation yields to="".
        resp = await nc.post(f"{BASE}/{active_event}/form-messages", json={
            "template_path": "ICS USA/ICS213.txt",
            "variables": {"Subject": "test", "MsgBody": "hello"},
            "datetime_stamp": "2026/07/17 18:30",
        })
        assert resp.status_code == 422

    async def test_empty_to_preview_422(self, nc, active_event, db_setup):
        """Preview with empty To: must also 422."""
        resp = await nc.post(f"{BASE}/{active_event}/forms/preview", json={
            "template_path": "ICS USA/ICS213.txt",
            "variables": {"Subject": "test", "MsgBody": "hello"},
            "datetime_stamp": "2026/07/17 18:30",
        })
        assert resp.status_code == 422


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
    """Tests for GET /events/{id}/messages/{mid}/reply-form."""

    def _seed_inbound_with_form(self, db_setup, event_id):
        """Insert an inbound EventMessage whose RawMessage carries form XML."""
        from datetime import datetime, timezone
        from backend.modules.checkins.models import MessageType, RawMessage, RawMessageAttachment

        with db_setup["factory"]() as db:
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

    def _seed_plain_inbound(self, db_setup, event_id):
        """Insert an inbound EventMessage with NO form (plain text)."""
        from datetime import datetime, timezone
        from backend.modules.checkins.models import MessageType, RawMessage

        with db_setup["factory"]() as db:
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

    async def test_reply_form_404_plain_message(self, nc, active_event, db_setup):
        """Plain inbound message (no RMS_Express_Form XML) → 404."""
        mid = self._seed_plain_inbound(db_setup, active_event)
        resp = await nc.get(f"{BASE}/{active_event}/messages/{mid}/reply-form")
        assert resp.status_code == 404

    async def test_reply_form_malformed_xml_returns_404(self, nc, active_event, db_setup):
        """An inbound message whose form XML is well-sniffed but malformed
        must return 404, not 500 (ET.ParseError must be caught)."""
        from datetime import datetime, timezone
        from backend.modules.checkins.models import MessageType, RawMessage, RawMessageAttachment

        # Sniffs as a form (passes the regex slice) but fails ET.fromstring parse.
        malformed_xml = "<RMS_Express_Form><bad></bad attr></RMS_Express_Form>"

        with db_setup["factory"]() as db:
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
            from backend.modules.events.models import EventMessage, MessageDirection, MessageStatus
            msg = EventMessage(
                event_id=active_event,
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
            mid = msg.id

        resp = await nc.get(f"{BASE}/{active_event}/messages/{mid}/reply-form")
        assert resp.status_code == 404

    async def test_reply_form_200_happy_path(self, nc, active_event, db_setup, monkeypatch):
        """Inbound message with captured form → reply_template_path + input_form_path + prefill."""
        import backend.modules.forms.library as lib

        # Point find_template at the same tmp forms dir the fixture already created.
        forms_dir = db_setup["forms"]
        monkeypatch.setattr(lib, "forms_library_dir", lambda: forms_dir)
        # Clear the module-level template cache so find_template re-scans from the patched dir.
        lib.clear_template_cache()

        mid = self._seed_inbound_with_form(db_setup, active_event)
        resp = await nc.get(f"{BASE}/{active_event}/messages/{mid}/reply-form")
        assert resp.status_code == 200
        body = resp.json()
        # reply_template resolves to "ICS USA/ICS213.txt" (relative to forms dir)
        assert body["reply_template_path"] == "ICS USA/ICS213.txt"
        # input_form_path resolves to "ICS USA/ICS213Input.html" (from the Form: line in the .txt)
        assert body["input_form_path"] == "ICS USA/ICS213Input.html"
        # prefill carries the variables from the inbound form XML
        assert body["prefill"]["msgbody"] == "all clear"
        assert body["prefill"]["tostation"] == "W0NC"
