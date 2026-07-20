import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.events.models import EventMessage, EventMessageForm, MessageDirection
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
