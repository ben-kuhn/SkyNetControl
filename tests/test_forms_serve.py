import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import create_app
from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.forms import serve as serve_mod
from backend.modules.nets.models import Net, NetMembership, NetRole
from tests.conftest import make_test_token


@pytest.fixture
def lib(tmp_path, monkeypatch):
    base = tmp_path / "forms"
    (base / "ICS USA").mkdir(parents=True)
    (base / "ICS USA" / "ICS213Input.html").write_text(
        "<html><body><form id='f'><input name='MsgBody'></form></body></html>"
    )
    monkeypatch.setattr(serve_mod, "forms_library_dir", lambda: base)
    return base


def test_render_injects_shim(lib):
    html = serve_mod.render_input_form("ICS USA/ICS213Input.html")
    assert "<form id='f'>" in html
    assert "skynet-form-vars" in html  # the shim posts this message type
    assert "postMessage" in html
    assert html.rstrip().endswith("</html>") or "</body>" in html


def test_prefill_seeded(lib):
    html = serve_mod.render_input_form("ICS USA/ICS213Input.html", prefill={"MsgBody": "hello"})
    assert '"MsgBody": "hello"' in html


def test_prefill_case_insensitive_matcher(lib):
    """The shim must match form field names case-insensitively.

    Real Winlink forms use mixed-case field names (e.g. MsgBody) while the
    prefill dict from extract_form_variables uses lowercased keys (msgbody).
    The shim must iterate [name] elements and compare lowercased rather than
    doing a case-sensitive CSS attribute selector.
    """
    # Render with a lowercase prefill key; form has mixed-case name attribute.
    html = serve_mod.render_input_form("ICS USA/ICS213Input.html", prefill={"msgbody": "hello"})
    # The new shim must iterate querySelectorAll('[name]') and lowercase-compare.
    assert "querySelectorAll('[name]')" in html or 'querySelectorAll("[name]")' in html
    assert "el.name.toLowerCase()" in html
    # The prefill must still be seeded in the JSON (with its lowercase key).
    assert '"msgbody": "hello"' in html or '"msgbody":"hello"' in html


def test_prefill_script_tag_escaped(lib):
    html = serve_mod.render_input_form(
        "ICS USA/ICS213Input.html",
        prefill={"MsgBody": "</script><script>alert(1)</script>"},
    )
    # The raw closing tag must NOT appear un-escaped inside the injected JSON —
    # it is escaped to <\/script>, so no premature script termination.
    assert "</script><script>alert(1)</script>" not in html
    assert "<\\/script>" in html


def test_traversal_blocked(lib):
    with pytest.raises(ValueError):
        serve_mod.render_input_form("../../etc/passwd")
    with pytest.raises(ValueError):
        serve_mod.render_input_form("ICS USA/../../secret")


def test_missing_form(lib):
    with pytest.raises(FileNotFoundError):
        serve_mod.render_input_form("ICS USA/Nope.html")


# Route tests


@pytest.fixture
def test_settings(tmp_path):
    return Settings(
        jwt_secret_key="test-secret",
        app_base_url="http://test",
        state_dir=str(tmp_path),
    )


@pytest.fixture
def db_setup(test_settings):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()

    # Create a test net
    net = Net(slug="t", name="Test Net")
    session.add(net)
    session.flush()

    # Create a net_control member (full perms)
    user_nc = User(
        callsign="NC0TST",
        oidc_subject="auth0|nc",
        name="NC Test",
        is_admin=False,
        is_pending=False,
    )
    session.add(user_nc)
    session.flush()
    session.add(NetMembership(net_id=net.id, user_callsign=user_nc.callsign, role=NetRole.NET_CONTROL))

    # Create a viewer member (limited perms)
    user_v = User(
        callsign="V0TST",
        oidc_subject="auth0|viewer",
        name="Viewer Test",
        is_admin=False,
        is_pending=False,
    )
    session.add(user_v)
    session.flush()
    session.add(NetMembership(net_id=net.id, user_callsign=user_v.callsign, role=NetRole.VIEWER))

    session.commit()
    session.close()

    return SessionLocal


@pytest.fixture
def test_app(test_settings, db_setup):
    app = create_app(settings=test_settings)
    app.state.session_factory = db_setup
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_render_route_net_control(test_client, test_settings, tmp_path, monkeypatch):
    """GET /api/nets/{slug}/forms/render?path=... returns form with shim + CSP (net_control auth)."""
    base = tmp_path / "forms"
    (base / "ICS USA").mkdir(parents=True)
    (base / "ICS USA" / "ICS213Input.html").write_text(
        "<html><body><form id='f'><input name='MsgBody'></form></body></html>"
    )
    monkeypatch.setattr(serve_mod, "forms_library_dir", lambda: base)

    token = make_test_token("NC0TST", test_settings, token_version=0)
    headers = {"cookie": f"access_token={token}"}

    resp = await test_client.get(
        "/api/nets/t/forms/render?path=ICS%20USA%2FICS213Input.html", headers=headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "<form id='f'>" in body
    assert "skynet-form-vars" in body
    csp = resp.headers["content-security-policy"]
    for directive in [
        "sandbox", "default-src 'none'", "script-src 'unsafe-inline'",
        "style-src 'unsafe-inline'", "img-src data:", "connect-src 'none'",
        "form-action 'none'",
    ]:
        assert directive in csp


@pytest.mark.asyncio
async def test_render_route_traversal_blocked(test_client, test_settings, tmp_path, monkeypatch):
    """GET /api/nets/{slug}/forms/render?path=../../x → 404 (traversal blocked)."""
    base = tmp_path / "forms"
    base.mkdir(parents=True)
    monkeypatch.setattr(serve_mod, "forms_library_dir", lambda: base)

    token = make_test_token("NC0TST", test_settings, token_version=0)
    headers = {"cookie": f"access_token={token}"}

    resp = await test_client.get("/api/nets/t/forms/render?path=../../etc/passwd", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_render_route_viewer_forbidden(test_client, test_settings, tmp_path, monkeypatch):
    """GET /api/nets/{slug}/forms/render by non-net_control member → 403."""
    base = tmp_path / "forms"
    (base / "ICS USA").mkdir(parents=True)
    (base / "ICS USA" / "ICS213Input.html").write_text("<form></form>")
    monkeypatch.setattr(serve_mod, "forms_library_dir", lambda: base)

    token = make_test_token("V0TST", test_settings, token_version=0)
    headers = {"cookie": f"access_token={token}"}

    resp = await test_client.get(
        "/api/nets/t/forms/render?path=ICS%20USA%2FICS213Input.html", headers=headers
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_render_route_prefill_seeded(test_client, test_settings, tmp_path, monkeypatch):
    """GET .../forms/render?path=...&prefill=<json> returns 200 with prefill JSON in shim."""
    import json
    import urllib.parse

    base = tmp_path / "forms"
    (base / "ICS USA").mkdir(parents=True)
    (base / "ICS USA" / "ICS213Input.html").write_text(
        "<html><body><form id='f'><input name='MsgBody'></form></body></html>"
    )
    monkeypatch.setattr(serve_mod, "forms_library_dir", lambda: base)

    token = make_test_token("NC0TST", test_settings, token_version=0)
    headers = {"cookie": f"access_token={token}"}

    prefill = {"MsgBody": "hello from reply"}
    prefill_param = urllib.parse.quote(json.dumps(prefill))
    resp = await test_client.get(
        f"/api/nets/t/forms/render?path=ICS%20USA%2FICS213Input.html&prefill={prefill_param}",
        headers=headers,
    )
    assert resp.status_code == 200
    assert "hello from reply" in resp.text
    assert "MsgBody" in resp.text


@pytest.mark.asyncio
async def test_render_route_prefill_malformed_returns_200(test_client, test_settings, tmp_path, monkeypatch):
    """GET .../forms/render?path=...&prefill=INVALID still returns 200 (falls back to empty, no 500)."""
    base = tmp_path / "forms"
    (base / "ICS USA").mkdir(parents=True)
    (base / "ICS USA" / "ICS213Input.html").write_text(
        "<html><body><form id='f'><input name='MsgBody'></form></body></html>"
    )
    monkeypatch.setattr(serve_mod, "forms_library_dir", lambda: base)

    token = make_test_token("NC0TST", test_settings, token_version=0)
    headers = {"cookie": f"access_token={token}"}

    resp = await test_client.get(
        "/api/nets/t/forms/render?path=ICS%20USA%2FICS213Input.html&prefill=NOT_VALID_JSON",
        headers=headers,
    )
    assert resp.status_code == 200
    assert "skynet-form-vars" in resp.text
