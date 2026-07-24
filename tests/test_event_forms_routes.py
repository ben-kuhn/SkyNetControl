"""Tests for GET /api/events/{id}/forms/catalog and /api/events/{id}/forms/render (CONTROL-gated)."""
import json
import urllib.parse

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import Settings
from backend.db.base import Base
from backend.auth.models import User
from backend.modules.events.models import Event, EventType, EventStatus
from backend.modules.forms import catalog as catalog_mod
from backend.modules.forms import serve as serve_mod
from backend.modules.events.forms_routes import event_forms_router
from tests.conftest import make_test_token


@pytest.fixture
def app_ctx():
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret")
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all(
            [
                User(callsign="OWNER", oidc_subject="x|o", name="O"),
                User(callsign="OTHER", oidc_subject="x|ot", name="T"),
            ]
        )
        priv = Event(
            name="P",
            event_type=EventType.EMERGENCY,
            status=EventStatus.ACTIVE,
            created_by="OWNER",
            public_token="ptok",
            visibility="private",
        )
        pub = Event(
            name="U",
            event_type=EventType.EMERGENCY,
            status=EventStatus.ACTIVE,
            created_by="OWNER",
            public_token="utok",
            visibility="public",
        )
        db.add_all([priv, pub])
        db.flush()
        db.commit()
        ids = {"event": priv.id, "public_event": pub.id}
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = settings
    app.include_router(event_forms_router)
    return app, settings, ids


def _control_client(app, settings):
    """Authenticated client as OWNER (CONTROL on the private event)."""
    token = make_test_token("OWNER", settings)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"access_token": token})


def _other_client(app, settings):
    """Authenticated client as OTHER (non-control on any event)."""
    token = make_test_token("OTHER", settings)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"access_token": token})


@pytest.mark.asyncio
async def test_event_forms_catalog_requires_control(app_ctx, tmp_path, monkeypatch):
    """CONTROL gets 200; non-control (even on a public event) gets 403."""
    app, settings, ids = app_ctx
    eid, pub = ids["event"], ids["public_event"]

    # Patch catalog library to avoid hitting real disk
    base = tmp_path / "forms"
    ics = base / "ICS USA"
    ics.mkdir(parents=True)
    (ics / "ICS213.txt").write_text("Form: ICS213Input.html\nMsg:\nx\n")
    (ics / "ICS213Input.html").write_text("<form></form>")
    monkeypatch.setattr(catalog_mod, "forms_library_dir", lambda: base)
    catalog_mod.clear_catalog_cache()

    async with _control_client(app, settings) as c:
        r = await c.get(f"/api/events/{eid}/forms/catalog")
        assert r.status_code == 200

    # Authenticated non-control on a public event → 403
    async with _other_client(app, settings) as c:
        r = await c.get(f"/api/events/{pub}/forms/catalog")
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_event_forms_catalog_returns_tree(app_ctx, tmp_path, monkeypatch):
    """Catalog route returns a JSON tree with folders/forms."""
    app, settings, ids = app_ctx
    eid = ids["event"]

    base = tmp_path / "forms"
    ics = base / "ICS USA"
    ics.mkdir(parents=True)
    (ics / "ICS213.txt").write_text("Form: ICS213Input.html\nMsg:\nx\n")
    (ics / "ICS213Input.html").write_text("<form></form>")
    monkeypatch.setattr(catalog_mod, "forms_library_dir", lambda: base)
    catalog_mod.clear_catalog_cache()

    async with _control_client(app, settings) as c:
        r = await c.get(f"/api/events/{eid}/forms/catalog")
        assert r.status_code == 200
        tree = r.json()
        folders = {f["name"]: f for f in tree["folders"]}
        assert "ICS USA" in folders
        assert len(folders["ICS USA"]["forms"]) == 1
        assert folders["ICS USA"]["forms"][0]["name"] == "ICS213"


@pytest.mark.asyncio
async def test_event_forms_catalog_filter(app_ctx, tmp_path, monkeypatch):
    """Catalog ?q= filter works (case-insensitive)."""
    app, settings, ids = app_ctx
    eid = ids["event"]

    base = tmp_path / "forms"
    ics = base / "ICS USA"
    ics.mkdir(parents=True)
    (ics / "ICS213.txt").write_text("Form: ICS213Input.html\nMsg:\nx\n")
    (ics / "ICS213Input.html").write_text("<form></form>")
    (ics / "ICS214.txt").write_text("Form: ICS214Input.html\nMsg:\nx\n")
    (ics / "ICS214Input.html").write_text("<form></form>")
    monkeypatch.setattr(catalog_mod, "forms_library_dir", lambda: base)
    catalog_mod.clear_catalog_cache()

    async with _control_client(app, settings) as c:
        r = await c.get(f"/api/events/{eid}/forms/catalog?q=ics213")
        assert r.status_code == 200
        tree = r.json()
        folders = {f["name"]: f for f in tree["folders"]}
        assert "ICS USA" in folders
        forms = folders["ICS USA"]["forms"]
        assert len(forms) == 1
        assert forms[0]["name"] == "ICS213"


@pytest.mark.asyncio
async def test_event_forms_render_returns_html(app_ctx, tmp_path, monkeypatch):
    """Render route returns 200 HTML with shim + sandbox CSP for CONTROL."""
    app, settings, ids = app_ctx
    eid = ids["event"]

    base = tmp_path / "forms"
    (base / "ICS USA").mkdir(parents=True)
    (base / "ICS USA" / "ICS213Input.html").write_text(
        "<html><body><form id='f'><input name='MsgBody'></form></body></html>"
    )
    monkeypatch.setattr(serve_mod, "forms_library_dir", lambda: base)

    async with _control_client(app, settings) as c:
        r = await c.get(f"/api/events/{eid}/forms/render?path=ICS%20USA%2FICS213Input.html")
        assert r.status_code == 200
        assert "<form id='f'>" in r.text
        assert "skynet-form-vars" in r.text
        csp = r.headers["content-security-policy"]
        for directive in [
            "sandbox", "default-src 'none'", "script-src 'unsafe-inline'",
            "style-src 'unsafe-inline'", "img-src data:", "connect-src 'none'",
            "form-action 'none'",
        ]:
            assert directive in csp


@pytest.mark.asyncio
async def test_event_forms_render_404_on_bad_path(app_ctx, tmp_path, monkeypatch):
    """Render route returns 404 for a path that doesn't exist."""
    app, settings, ids = app_ctx
    eid = ids["event"]

    base = tmp_path / "forms"
    base.mkdir(parents=True)
    monkeypatch.setattr(serve_mod, "forms_library_dir", lambda: base)

    async with _control_client(app, settings) as c:
        r = await c.get(f"/api/events/{eid}/forms/render?path=__does_not_exist__")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_event_forms_render_traversal_blocked(app_ctx, tmp_path, monkeypatch):
    """Render route returns 404 for path traversal attempts."""
    app, settings, ids = app_ctx
    eid = ids["event"]

    base = tmp_path / "forms"
    base.mkdir(parents=True)
    monkeypatch.setattr(serve_mod, "forms_library_dir", lambda: base)

    async with _control_client(app, settings) as c:
        r = await c.get(f"/api/events/{eid}/forms/render?path=../../etc/passwd")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_event_forms_render_requires_control(app_ctx, tmp_path, monkeypatch):
    """Render route returns 403 for non-control user."""
    app, settings, ids = app_ctx
    eid, pub = ids["event"], ids["public_event"]

    base = tmp_path / "forms"
    (base / "ICS USA").mkdir(parents=True)
    (base / "ICS USA" / "ICS213Input.html").write_text("<form></form>")
    monkeypatch.setattr(serve_mod, "forms_library_dir", lambda: base)

    async with _other_client(app, settings) as c:
        r = await c.get(f"/api/events/{pub}/forms/render?path=ICS%20USA%2FICS213Input.html")
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_event_forms_render_prefill_seeded(app_ctx, tmp_path, monkeypatch):
    """Render route seeds prefill JSON into the form shim."""
    app, settings, ids = app_ctx
    eid = ids["event"]

    base = tmp_path / "forms"
    (base / "ICS USA").mkdir(parents=True)
    (base / "ICS USA" / "ICS213Input.html").write_text(
        "<html><body><form id='f'><input name='MsgBody'></form></body></html>"
    )
    monkeypatch.setattr(serve_mod, "forms_library_dir", lambda: base)

    prefill = {"MsgBody": "hello from event"}
    prefill_param = urllib.parse.quote(json.dumps(prefill))
    async with _control_client(app, settings) as c:
        r = await c.get(
            f"/api/events/{eid}/forms/render?path=ICS%20USA%2FICS213Input.html&prefill={prefill_param}"
        )
        assert r.status_code == 200
        assert "hello from event" in r.text


@pytest.mark.asyncio
async def test_event_forms_render_malformed_prefill_returns_200(app_ctx, tmp_path, monkeypatch):
    """Malformed prefill JSON does not 500 — falls back to empty."""
    app, settings, ids = app_ctx
    eid = ids["event"]

    base = tmp_path / "forms"
    (base / "ICS USA").mkdir(parents=True)
    (base / "ICS USA" / "ICS213Input.html").write_text(
        "<html><body><form id='f'><input name='MsgBody'></form></body></html>"
    )
    monkeypatch.setattr(serve_mod, "forms_library_dir", lambda: base)

    async with _control_client(app, settings) as c:
        r = await c.get(
            f"/api/events/{eid}/forms/render?path=ICS%20USA%2FICS213Input.html&prefill=NOT_VALID_JSON"
        )
        assert r.status_code == 200
        assert "skynet-form-vars" in r.text
