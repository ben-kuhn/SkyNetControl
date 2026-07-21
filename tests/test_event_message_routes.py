import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.checkins.models import MessageType, RawMessage
from backend.modules.events.messages import route_event_messages
from backend.modules.nets.config_service import set_net_config_bulk
from backend.modules.nets.models import Net, NetMembership, NetRole
from tests.conftest import make_test_token

NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/events"


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)


@pytest.fixture
def db_setup():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        nc = User(callsign="W0NC", oidc_subject="auth0|nc", name="NC")
        viewer = User(callsign="KD0TST", oidc_subject="auth0|v", name="V")
        net = Net(slug=NET_SLUG, name="Test Net", is_public=False)
        session.add_all([nc, viewer, net])
        session.flush()
        session.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        session.add(NetMembership(user_callsign="KD0TST", net_id=net.id, role=NetRole.VIEWER))
        set_net_config_bulk(session, net.id, {"net_address": "W0NE@winlink.org"})
        session.commit()
        yield {"engine": engine, "factory": factory, "net_id": net.id}
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


@pytest.fixture
async def viewer_client(app, test_settings):
    token = make_test_token("KD0TST", test_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token}) as c:
        yield c


@pytest.fixture
async def active_event(nc_client):
    resp = await nc_client.post(BASE, json={"name": "E", "event_type": "emergency", "activate": True})
    return resp.json()["id"]


def _seed_inbound(db_setup, event_id, mid="M1", frm="KE0XYZ@winlink.org", subj="SITREP"):
    from datetime import datetime, timezone
    with db_setup["factory"]() as db:
        db.add(RawMessage(
            message_id=mid, from_address=frm, received_at=datetime.now(timezone.utc),
            subject=subj, body="body", message_type=MessageType.UNKNOWN, parsed=False,
        ))
        db.commit()
        route_event_messages(db, db_setup["net_id"], [{
            "message_id": mid, "from_address": frm, "to_address": "W0NE@winlink.org",
            "subject": subj, "body": "body", "received_at": datetime.now(timezone.utc),
        }])


class TestListMessages:
    async def test_lists_inbound(self, nc_client, viewer_client, active_event, db_setup):
        _seed_inbound(db_setup, active_event)
        resp = await viewer_client.get(f"{BASE}/{active_event}/messages", params={"since": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert body["latest_msg_seq"] == 1
        assert len(body["messages"]) == 1
        assert body["messages"][0]["direction"] == "inbound"
        assert body["messages"][0]["from_callsign"] == "KE0XYZ"

    async def test_cursor_delta(self, nc_client, active_event, db_setup):
        _seed_inbound(db_setup, active_event, mid="M1")
        _seed_inbound(db_setup, active_event, mid="M2")
        resp = await nc_client.get(f"{BASE}/{active_event}/messages", params={"since": 1})
        body = resp.json()
        assert [m["msg_seq"] for m in body["messages"]] == [2]

    async def test_dismissed_hidden_by_default(self, nc_client, active_event, db_setup):
        _seed_inbound(db_setup, active_event)
        mid = (await nc_client.get(f"{BASE}/{active_event}/messages")).json()["messages"][0]["id"]
        await nc_client.patch(f"{BASE}/{active_event}/messages/{mid}", json={"status": "dismissed"})
        assert (await nc_client.get(f"{BASE}/{active_event}/messages")).json()["messages"] == []
        withd = await nc_client.get(f"{BASE}/{active_event}/messages", params={"include_dismissed": "true"})
        assert len(withd.json()["messages"]) == 1


class TestCompose:
    async def test_send(self, nc_client, active_event):
        resp = await nc_client.post(f"{BASE}/{active_event}/messages", json={
            "to_address": "jane@redcross.org", "subject": "Status", "body": "all clear",
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["message"]["direction"] == "outbound"
        assert body["message"]["to_address"] == "jane@redcross.org"
        assert "delivered" in body

    async def test_bad_address_422(self, nc_client, active_event):
        resp = await nc_client.post(f"{BASE}/{active_event}/messages", json={
            "to_address": "   ", "subject": "s", "body": "b"})
        assert resp.status_code == 422

    async def test_send_closed_event_409(self, nc_client, active_event):
        await nc_client.post(f"{BASE}/{active_event}/close")
        resp = await nc_client.post(f"{BASE}/{active_event}/messages", json={
            "to_address": "a@b.org", "subject": "s", "body": "b"})
        assert resp.status_code == 409

    async def test_viewer_cannot_send(self, viewer_client, active_event):
        resp = await viewer_client.post(f"{BASE}/{active_event}/messages", json={
            "to_address": "a@b.org", "subject": "s", "body": "b"})
        assert resp.status_code == 403


class TestPatchStatus:
    async def test_mark_read(self, nc_client, active_event, db_setup):
        _seed_inbound(db_setup, active_event)
        mid = (await nc_client.get(f"{BASE}/{active_event}/messages")).json()["messages"][0]["id"]
        resp = await nc_client.patch(f"{BASE}/{active_event}/messages/{mid}", json={"status": "read"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "read"

    async def test_missing_404(self, nc_client, active_event):
        resp = await nc_client.patch(f"{BASE}/{active_event}/messages/9999", json={"status": "read"})
        assert resp.status_code == 404


class TestRescan:
    async def test_rescan_reports_count(self, nc_client, active_event, monkeypatch, db_setup):
        from datetime import datetime, timezone
        import backend.modules.events.routes as events_routes
        from backend.modules.nets.config_service import set_net_config

        # rescan_route requires both pat_mailbox_path and net_address to be set
        with db_setup["factory"]() as db:
            set_net_config(db, db_setup["net_id"], "pat_mailbox_path", "/tmp/fake-mailbox")
            db.commit()

        def fake_read(inbox_path, net_address):
            return [{
                "message_id": "RS1", "from_address": "KE0XYZ@winlink.org", "to_address": net_address,
                "subject": "hi", "body": "b", "received_at": datetime.now(timezone.utc), "path": None,
            }]
        monkeypatch.setattr(events_routes, "read_mailbox", fake_read)
        resp = await nc_client.post(f"{BASE}/{active_event}/rescan")
        assert resp.status_code == 200
        assert resp.json()["new_messages"] == 1

    async def test_rescan_closed_event_409(self, nc_client, active_event):
        await nc_client.post(f"{BASE}/{active_event}/close")
        resp = await nc_client.post(f"{BASE}/{active_event}/rescan")
        assert resp.status_code == 409


class TestPatchStatusClosedEvent:
    async def test_closed_event_patch_409(self, nc_client, active_event, db_setup):
        """IMPORTANT 3: PATCH message status on a closed event must return 409."""
        _seed_inbound(db_setup, active_event)
        mid = (await nc_client.get(f"{BASE}/{active_event}/messages")).json()["messages"][0]["id"]
        # Close the event
        await nc_client.post(f"{BASE}/{active_event}/close")
        # Now PATCH status on closed event should 409
        resp = await nc_client.patch(f"{BASE}/{active_event}/messages/{mid}", json={"status": "read"})
        assert resp.status_code == 409


class TestDeliveryStatus:
    async def test_outbound_queued_delivery_status(self, nc_client, active_event, db_setup):
        """Outbound message with a QUEUED winlink DeliveryLog exposes delivery_status='queued'."""
        from datetime import datetime, timezone

        from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus

        # Send an outbound message to get its id.
        resp = await nc_client.post(f"{BASE}/{active_event}/messages", json={
            "to_address": "dest@example.com", "subject": "Test", "body": "hello",
        })
        assert resp.status_code == 201
        message_id = resp.json()["message"]["id"]

        # Update the existing DeliveryLog row to QUEUED status.
        with db_setup["factory"]() as db:
            log = (
                db.query(DeliveryLog)
                .filter(
                    DeliveryLog.content_type == "event_message",
                    DeliveryLog.content_id == message_id,
                    DeliveryLog.backend == "winlink",
                )
                .one_or_none()
            )
            if log is not None:
                log.status = DeliveryStatus.QUEUED
            else:
                db.add(DeliveryLog(
                    content_type="event_message",
                    content_id=message_id,
                    backend="winlink",
                    status=DeliveryStatus.QUEUED,
                    created_at=datetime.now(timezone.utc),
                ))
            db.commit()

        # List messages and check delivery_status.
        resp = await nc_client.get(f"{BASE}/{active_event}/messages")
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        outbound = [m for m in msgs if m["direction"] == "outbound" and m["id"] == message_id]
        assert len(outbound) == 1
        assert outbound[0]["delivery_status"] == "queued"

    async def test_inbound_has_no_delivery_status(self, nc_client, active_event, db_setup):
        """Inbound messages always have delivery_status=None."""
        _seed_inbound(db_setup, active_event)
        resp = await nc_client.get(f"{BASE}/{active_event}/messages")
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        inbound = [m for m in msgs if m["direction"] == "inbound"]
        assert len(inbound) == 1
        assert inbound[0]["delivery_status"] is None


class TestMessagingConfigured:
    async def test_messaging_configured_true_when_both_set(self, nc_client, active_event, db_setup):
        """messaging_configured=True when pat_mailbox_path and net_address both set."""
        from backend.modules.nets.config_service import set_net_config
        with db_setup["factory"]() as db:
            set_net_config(db, db_setup["net_id"], "pat_mailbox_path", "/tmp/mailbox")
            db.commit()
        resp = await nc_client.get(f"{BASE}/{active_event}/messages")
        assert resp.status_code == 200
        assert resp.json()["messaging_configured"] is True

    async def test_messaging_configured_false_when_mailbox_missing(self, nc_client, active_event):
        """messaging_configured=False when pat_mailbox_path is not set (net_address only)."""
        resp = await nc_client.get(f"{BASE}/{active_event}/messages")
        assert resp.status_code == 200
        # net_address is set (W0NE@winlink.org) but pat_mailbox_path is not
        assert resp.json()["messaging_configured"] is False

    async def test_messaging_configured_true_when_pat_transport_enabled(
        self, nc_client, active_event, db_setup, monkeypatch
    ):
        """Fix C: messaging_configured=True for a remote-PAT net with no mailbox path."""
        import backend.modules.events.routes as events_routes
        from backend.modules.nets.config_service import set_net_config

        # Set up PAT transport keys (no mailbox path).
        with db_setup["factory"]() as db:
            set_net_config(db, db_setup["net_id"], "pat_transport_enabled", "true")
            set_net_config(db, db_setup["net_id"], "pat_http_base_url", "http://pat.local:8080")
            db.commit()

        resp = await nc_client.get(f"{BASE}/{active_event}/messages")
        assert resp.status_code == 200
        assert resp.json()["messaging_configured"] is True, (
            "Remote-PAT net with no mailbox must still report messaging_configured=True"
        )


class TestRescanPatTransport:
    async def test_rescan_uses_pat_http_when_transport_enabled(
        self, nc_client, active_event, db_setup, monkeypatch
    ):
        """Fix C: rescan_route imports via PAT HTTP when pat_transport_enabled=true."""
        from datetime import datetime, timezone
        import backend.integrations.winlink.pat_inbound as pat_inbound_mod
        import backend.integrations.winlink.pat_config as pat_config_mod
        from backend.modules.nets.config_service import set_net_config

        with db_setup["factory"]() as db:
            set_net_config(db, db_setup["net_id"], "pat_transport_enabled", "true")
            set_net_config(db, db_setup["net_id"], "pat_http_base_url", "http://pat.local:8080")
            db.commit()

        def fake_fetch_inbound(client):
            return [{
                "message_id": "PAT-RS1", "from_address": "KE0XYZ@winlink.org",
                "to_address": "W0NE@winlink.org",
                "subject": "SITREP", "body": "all clear",
                "received_at": datetime.now(timezone.utc),
            }]

        monkeypatch.setattr(pat_inbound_mod, "fetch_inbound_messages", fake_fetch_inbound)

        # Patch build_pat_client so no real HTTP call is made.
        monkeypatch.setattr(pat_config_mod, "build_pat_client", lambda cfg: object())

        resp = await nc_client.post(f"{BASE}/{active_event}/rescan")
        assert resp.status_code == 200
        assert resp.json()["new_messages"] == 1
