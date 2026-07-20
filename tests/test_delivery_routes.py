import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from tests.conftest import make_test_token
from backend.config_mgmt.models import AppConfig
from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.integrations.delivery.routes import delivery_router
from backend.config import Settings
from backend.modules.nets.config_service import set_net_config


NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/delivery"


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )


@pytest.fixture
def db_setup():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(
            User(
                callsign="ADMIN",
                oidc_subject="local|admin",
                name="Admin User",
                email="admin@test.com",
                is_admin=True,
            )
        )
        from backend.modules.nets.models import Net
        from backend.modules.reminders.models import ReminderLog
        from backend.modules.schedule.models import NetSeason, NetSession, SessionType
        from datetime import date as _date

        net = Net(slug=NET_SLUG, name="Test Net")
        session.add(net)
        session.flush()
        season = NetSeason(
            net_id=net.id, name="S",
            start_date=_date(2026, 1, 1), end_date=_date(2026, 3, 31),
            day_of_week=3,
        )
        session.add(season)
        session.flush()
        net_session = NetSession(
            id=1,
            season_id=season.id,
            start_date=_date(2026, 1, 7), end_date=_date(2026, 1, 7),
            grace_period_hours=24.0, session_type=SessionType.REGULAR_CHECKIN,
        )
        session.add(net_session)
        session.flush()
        # Tests reference reminder content_id=1 — seed a ReminderLog with that id
        # so the new cross-net guard can resolve it back to this net.
        session.add(ReminderLog(
            id=1, session_id=net_session.id,
            content_subject="", content_body="",
            drafted_at=datetime.now(tz=timezone.utc),
        ))
        session.commit()
    return factory


@pytest.fixture
def app(test_settings, db_setup):
    application = FastAPI()
    application.state.session_factory = db_setup
    application.state.settings = test_settings
    application.include_router(delivery_router, prefix="/api/nets/{net_slug}/delivery")
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _auth_headers(test_settings, callsign="ADMIN", is_admin=True):
    token = make_test_token(callsign, test_settings, is_admin=is_admin, token_version=0)
    return {"Cookie": f"access_token={token}"}


@pytest.mark.anyio
async def test_get_delivery_status(client, test_settings, db_setup):
    with db_setup() as session:
        session.add(
            DeliveryLog(
                content_type="reminder",
                content_id=1,
                backend="email",
                status=DeliveryStatus.SENT,
                created_at=datetime.now(tz=timezone.utc),
                sent_at=datetime.now(tz=timezone.utc),
            )
        )
        session.commit()

    resp = await client.get(
        f"{BASE}/reminder/1",
        headers=_auth_headers(test_settings),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["backend"] == "email"
    assert data[0]["status"] == "sent"


@pytest.mark.anyio
async def test_get_delivery_status_unknown_id_returns_404(client, test_settings):
    """Conflate 'not found' with 'cross-net' so existence isn't probeable."""
    resp = await client.get(
        f"{BASE}/reminder/999",
        headers=_auth_headers(test_settings),
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_retry_delivery(client, test_settings, db_setup):
    with db_setup() as session:
        session.add(
            DeliveryLog(
                content_type="reminder",
                content_id=1,
                backend="email",
                status=DeliveryStatus.FAILED,
                error_message="SMTP down",
                created_at=datetime.now(tz=timezone.utc),
            )
        )
        session.commit()
        from backend.modules.nets.models import Net
        net = session.query(Net).filter_by(slug=NET_SLUG).one()
        set_net_config(session, net.id, "delivery.email.to_address", "net@test.com")

    with patch("backend.integrations.delivery.service.get_backend") as mock_get:
        from backend.integrations.delivery.backends.base import DeliveryResult

        mock_backend = type(
            "MockBackend", (), {"send": lambda self, s, b, c: DeliveryResult(success=True, error=None)}
        )()
        mock_get.return_value = mock_backend

        resp = await client.post(
            f"{BASE}/reminder/1/retry",
            headers=_auth_headers(test_settings),
        )

    assert resp.status_code == 200
    assert resp.json()["retried"] is True


@pytest.mark.anyio
async def test_retry_requires_auth(client):
    resp = await client.post(f"{BASE}/reminder/1/retry")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_get_delivery_status_rejects_cross_net(client, test_settings, db_setup):
    """IDOR guard: a reminder log on net 'other' can't be read via net 't'."""
    from datetime import date as _date

    from backend.modules.nets.models import Net
    from backend.modules.reminders.models import ReminderLog
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType

    with db_setup() as session:
        other = Net(slug="other", name="Other Net")
        session.add(other)
        session.flush()
        season = NetSeason(
            net_id=other.id, name="O",
            start_date=_date(2026, 1, 1), end_date=_date(2026, 3, 31),
            day_of_week=3,
        )
        session.add(season); session.flush()
        sess = NetSession(
            id=2,
            season_id=season.id,
            start_date=_date(2026, 1, 7), end_date=_date(2026, 1, 7),
            grace_period_hours=24.0, session_type=SessionType.REGULAR_CHECKIN,
        )
        session.add(sess); session.flush()
        session.add(ReminderLog(
            id=2, session_id=sess.id,
            content_subject="", content_body="",
            drafted_at=datetime.now(tz=timezone.utc),
        ))
        session.commit()

    resp = await client.get(
        f"{BASE}/reminder/2",
        headers=_auth_headers(test_settings),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Event-message retry: form attachment must be rebuilt on retry
# ---------------------------------------------------------------------------

@pytest.fixture
def form_retry_db_setup(tmp_path):
    """DB + forms dir for testing that form-message retry re-attaches the XML."""
    # Forms library with one template that has a fixed recipient.
    forms = tmp_path / "forms"
    (forms / "ICS USA").mkdir(parents=True)
    (forms / "ICS USA" / "ICS213.txt").write_text(
        "Form: ICS213Input.html\nTo: KE0XYZ\nSubject: ICS213 Report\nMsg:\n<Var MsgBody>\n"
    )
    (forms / "ICS USA" / "ICS213Input.html").write_text("<form><input name='MsgBody'></form>")

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        session.add(User(callsign="ADMIN", oidc_subject="local|admin", name="Admin", is_admin=True))
        from backend.modules.nets.models import Net, NetMembership, NetRole
        from backend.modules.events.models import Event, EventMessage, EventMessageForm, EventStatus, MessageDirection, MessageStatus
        net = Net(slug=NET_SLUG, name="Test Net")
        session.add(net)
        session.flush()
        session.add(NetMembership(user_callsign="ADMIN", net_id=net.id, role=NetRole.NET_CONTROL))
        set_net_config(session, net.id, "net_address", "W0NE@winlink.org")
        set_net_config(session, net.id, "pat_mailbox_path", str(tmp_path / "mailbox"))

        event = Event(
            net_id=net.id, name="E", event_type="emergency",
            status=EventStatus.ACTIVE, created_by="ADMIN",
        )
        session.add(event)
        session.flush()

        msg = EventMessage(
            event_id=event.id, msg_seq=1,
            direction=MessageDirection.OUTBOUND,
            from_callsign="W0NE", to_address="KE0XYZ",
            subject="ICS213 Report", body="all clear",
            status=MessageStatus.READ, actor="ADMIN",
        )
        session.add(msg)
        session.flush()
        session.add(EventMessageForm(
            event_message_id=msg.id,
            template_path="ICS USA/ICS213.txt",
            display_form="ICS213Input.html",
            variables={"MsgBody": "all clear"},
            datetime_stamp="2026/07/17 18:30",
        ))
        session.add(DeliveryLog(
            content_type="event_message",
            content_id=msg.id,
            backend="winlink",
            status=DeliveryStatus.FAILED,
            error_message="PAT not running",
            created_at=datetime.now(tz=timezone.utc),
        ))
        session.commit()
        msg_id = msg.id

    return {"factory": factory, "engine": engine, "msg_id": msg_id, "forms": forms}


@pytest.fixture
def form_retry_app(test_settings, form_retry_db_setup):
    application = FastAPI()
    application.state.session_factory = form_retry_db_setup["factory"]
    application.state.settings = test_settings
    application.include_router(delivery_router, prefix="/api/nets/{net_slug}/delivery")
    return application


@pytest.fixture
async def form_retry_client(form_retry_app):
    transport = ASGITransport(app=form_retry_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.anyio
async def test_form_message_retry_includes_attachment(form_retry_client, test_settings, form_retry_db_setup, monkeypatch):
    """Retrying a failed form event-message must rebuild the RMS_Express_Form
    XML and pass it as an attachment — not silently re-send plain text."""
    import backend.modules.forms.builder as bld
    monkeypatch.setattr(bld, "forms_library_dir", lambda: form_retry_db_setup["forms"])

    captured_configs = []

    def mock_send(self, subject, body, config):
        captured_configs.append(dict(config))
        from backend.integrations.delivery.backends.base import DeliveryResult
        return DeliveryResult(success=True, error=None)

    from backend.integrations.delivery.backends.winlink import WinlinkBackend
    monkeypatch.setattr(WinlinkBackend, "send", mock_send)

    resp = await form_retry_client.post(
        f"{BASE}/event_message/{form_retry_db_setup['msg_id']}/retry",
        headers=_auth_headers(test_settings),
    )
    assert resp.status_code == 200
    assert resp.json()["retried"] is True
    # The attachment must be present in the config sent to the backend.
    assert len(captured_configs) == 1
    atts = captured_configs[0].get("attachments") or []
    assert len(atts) == 1
    # Verify it's the RMS_Express_Form XML.
    xml_bytes = atts[0].data
    assert b"<RMS_Express_Form>" in xml_bytes
    assert b"all clear" in xml_bytes


@pytest.mark.anyio
async def test_form_message_retry_rebuild_failure_returns_422(
    form_retry_client, test_settings, form_retry_db_setup, monkeypatch, tmp_path
):
    """If the form can't be rebuilt on retry (e.g. its template is gone from the
    library), the retry must fail loudly with 422 — never silently degrade to a
    plain-text send that reports success."""
    import backend.modules.forms.builder as bld
    # Point the library at an empty dir so the template can't be resolved.
    empty = tmp_path / "empty-forms"
    empty.mkdir()
    monkeypatch.setattr(bld, "forms_library_dir", lambda: empty)

    sent = []

    def mock_send(self, subject, body, config):
        sent.append(dict(config))
        from backend.integrations.delivery.backends.base import DeliveryResult
        return DeliveryResult(success=True, error=None)

    from backend.integrations.delivery.backends.winlink import WinlinkBackend
    monkeypatch.setattr(WinlinkBackend, "send", mock_send)

    resp = await form_retry_client.post(
        f"{BASE}/event_message/{form_retry_db_setup['msg_id']}/retry",
        headers=_auth_headers(test_settings),
    )
    assert resp.status_code == 422
    # And nothing was delivered — no plain-text fallback went out.
    assert sent == []


@pytest.mark.anyio
async def test_plain_message_retry_no_attachment(test_settings, db_setup):
    """Retrying a plain (non-form) event message must not add any attachment."""
    from backend.modules.events.models import Event, EventMessage, EventStatus, MessageDirection, MessageStatus

    with db_setup() as session:
        from backend.modules.nets.models import Net, NetMembership, NetRole
        net = session.query(Net).filter_by(slug=NET_SLUG).one()
        session.add(NetMembership(user_callsign="ADMIN", net_id=net.id, role=NetRole.NET_CONTROL))
        set_net_config(session, net.id, "net_address", "W0NE@winlink.org")
        set_net_config(session, net.id, "pat_mailbox_path", "/tmp/mailbox")

        event = Event(
            net_id=net.id, name="PlainEvt", event_type="emergency",
            status=EventStatus.ACTIVE, created_by="ADMIN",
        )
        session.add(event)
        session.flush()

        msg = EventMessage(
            event_id=event.id, msg_seq=1,
            direction=MessageDirection.OUTBOUND,
            from_callsign="W0NE", to_address="KE0ABC",
            subject="Plain msg", body="hello",
            status=MessageStatus.READ, actor="ADMIN",
        )
        session.add(msg)
        session.flush()
        session.add(DeliveryLog(
            content_type="event_message",
            content_id=msg.id,
            backend="winlink",
            status=DeliveryStatus.FAILED,
            error_message="PAT not running",
            created_at=datetime.now(tz=timezone.utc),
        ))
        session.commit()
        msg_id = msg.id

    captured_configs = []

    application = FastAPI()
    application.state.session_factory = db_setup
    application.state.settings = test_settings
    application.include_router(delivery_router, prefix="/api/nets/{net_slug}/delivery")

    from backend.integrations.delivery.backends.winlink import WinlinkBackend

    original_send = WinlinkBackend.send

    def mock_send(self, subject, body, config):
        captured_configs.append(dict(config))
        from backend.integrations.delivery.backends.base import DeliveryResult
        return DeliveryResult(success=True, error=None)

    WinlinkBackend.send = mock_send
    try:
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                f"{BASE}/event_message/{msg_id}/retry",
                headers=_auth_headers(test_settings),
            )
        assert resp.status_code == 200
        assert len(captured_configs) == 1
        # Plain message must not carry an attachments key (or it's empty).
        atts = captured_configs[0].get("attachments") or []
        assert atts == []
    finally:
        WinlinkBackend.send = original_send
