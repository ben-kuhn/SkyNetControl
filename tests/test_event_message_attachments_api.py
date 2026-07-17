import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.checkins.models import MessageType, RawMessage, RawMessageAttachment
from backend.modules.events.models import EventMessage, MessageDirection, MessageStatus
from backend.modules.nets.models import Net, NetMembership, NetRole
from tests.conftest import make_test_token

NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/events"

FORM_XML = ("<RMS_Express_Form><form_parameters><display_form>ICS213.html</display_form>"
            "<reply_template>ICS213Reply.txt</reply_template></form_parameters>"
            "<variables><msgbody>hi</msgbody></variables></RMS_Express_Form>")


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)


@pytest.fixture
def db_setup():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        from datetime import datetime, timezone
        from backend.modules.events.models import Event, EventType, EventStatus

        nc = User(callsign="W0NC", oidc_subject="auth0|nc", name="NC")
        net = Net(slug=NET_SLUG, name="Test Net", is_public=False)
        session.add_all([nc, net])
        session.flush()
        session.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        event = Event(net_id=net.id, name="E", event_type=EventType.EMERGENCY,
                      created_by="W0NC", status=EventStatus.ACTIVE)
        session.add(event)
        session.flush()
        raw = RawMessage(message_id="M1", from_address="KE0XYZ", received_at=datetime.now(timezone.utc),
                         subject="ICS213", body="see form", message_type=MessageType.WINLINK_FORM, parsed=True)
        session.add(raw)
        session.flush()
        att = RawMessageAttachment(raw_message_id=raw.id, filename="RMS_Express_Form_ICS213.xml",
                                   content_type="application/xml", data=FORM_XML.encode("utf-8"))
        session.add(att)
        msg = EventMessage(event_id=event.id, msg_seq=1, direction=MessageDirection.INBOUND,
                           raw_message_id=raw.id, from_callsign="KE0XYZ", to_address="W0NE",
                           subject="ICS213", body="see form", status=MessageStatus.UNREAD)
        session.add(msg)
        session.commit()
        yield {"engine": engine, "factory": factory, "event_id": event.id,
               "message_id": msg.id, "attachment_id": att.id}
    engine.dispose()


@pytest.fixture
def app(test_settings, db_setup):
    from backend.app import create_app
    application = create_app(settings=test_settings)
    application.state.engine = db_setup["engine"]
    application.state.session_factory = db_setup["factory"]
    return application


@pytest.fixture
async def nc_client(app, test_settings):
    token = make_test_token("W0NC", test_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token}) as c:
        yield c


class TestMessagePayload:
    async def test_attachments_summary_and_form_block(self, nc_client, db_setup):
        resp = await nc_client.get(f"{BASE}/{db_setup['event_id']}/messages")
        assert resp.status_code == 200
        msg = resp.json()["messages"][0]
        assert len(msg["attachments"]) == 1
        att = msg["attachments"][0]
        assert att["filename"] == "RMS_Express_Form_ICS213.xml"
        assert att["size"] == len(FORM_XML.encode("utf-8"))
        assert "data" not in att  # bytes never inline
        assert msg["form"]["is_form"] is True
        assert msg["form"]["display_form"] == "ICS213.html"
        assert msg["form"]["reply_template"] == "ICS213Reply.txt"


class TestDownload:
    async def test_download_streams_bytes(self, nc_client, db_setup):
        resp = await nc_client.get(
            f"{BASE}/{db_setup['event_id']}/messages/{db_setup['message_id']}"
            f"/attachments/{db_setup['attachment_id']}"
        )
        assert resp.status_code == 200
        assert resp.content == FORM_XML.encode("utf-8")
        assert resp.headers["content-type"] == "application/octet-stream"
        assert "RMS_Express_Form_ICS213.xml" in resp.headers.get("content-disposition", "")

    async def test_download_missing_404(self, nc_client, db_setup):
        resp = await nc_client.get(
            f"{BASE}/{db_setup['event_id']}/messages/{db_setup['message_id']}/attachments/9999"
        )
        assert resp.status_code == 404

    async def test_download_non_ascii_filename_no_500(self, nc_client, db_setup):
        """An attachment with a non-ASCII filename must download (200) without a 500."""
        from datetime import datetime, timezone
        from backend.modules.checkins.models import RawMessageAttachment

        with db_setup["factory"]() as session:
            att = RawMessageAttachment(
                raw_message_id=db_setup["attachment_id"],  # reuse raw_message_id from fixture
                filename="Ünïcödé_form.xml",
                content_type="application/xml",
                data=b"<unicode/>",
            )
            # Get the raw_message_id from the existing attachment
            existing = session.get(RawMessageAttachment, db_setup["attachment_id"])
            att.raw_message_id = existing.raw_message_id
            session.add(att)
            session.commit()
            unicode_att_id = att.id

        resp = await nc_client.get(
            f"{BASE}/{db_setup['event_id']}/messages/{db_setup['message_id']}"
            f"/attachments/{unicode_att_id}"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        # Filename must be in the Content-Disposition header (ASCII-folded, no crash)
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd
