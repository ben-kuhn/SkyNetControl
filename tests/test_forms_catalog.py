import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import create_app
from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.forms import catalog as catalog_mod
from backend.modules.nets.models import Net, NetMembership, NetRole
from tests.conftest import make_test_token


@pytest.fixture
def forms_tree(tmp_path, monkeypatch):
    # Build a fake forms library: a composable form (txt + input html) in a folder,
    # a display-only txt (no input html) that must be excluded.
    base = tmp_path / "forms"
    ics = base / "ICS USA"
    ics.mkdir(parents=True)
    (ics / "ICS213.txt").write_text("Form: ICS213Input.html,ICS213Viewer.html\nSubject: <Var Subject>\nMsg:\n<Var MsgBody>\n")
    (ics / "ICS213Input.html").write_text("<html><body><form></form></body></html>")
    (base / "DisplayOnly.txt").write_text("Subject: x\nMsg:\nnope\n")  # no input form → excluded
    monkeypatch.setattr(catalog_mod, "forms_library_dir", lambda: base)
    catalog_mod.clear_catalog_cache()
    return base


def test_catalog_trees_composable_forms(forms_tree):
    tree = catalog_mod.build_catalog()
    # top-level has the "ICS USA" folder
    folders = {f["name"]: f for f in tree["folders"]}
    assert "ICS USA" in folders
    forms = folders["ICS USA"]["forms"]
    assert len(forms) == 1
    assert forms[0]["name"] == "ICS213"
    assert forms[0]["input_form_path"].endswith("ICS213Input.html")


def test_display_only_excluded(forms_tree):
    tree = catalog_mod.build_catalog()
    # DisplayOnly.txt (no input form) must not appear anywhere
    def all_form_names(node):
        names = [f["name"] for f in node["forms"]]
        for sub in node["folders"]:
            names += all_form_names(sub)
        return names
    assert "DisplayOnly" not in all_form_names(tree)


def test_catalog_cache_keyed_and_clearable(forms_tree, monkeypatch):
    catalog_mod.clear_catalog_cache()
    t1 = catalog_mod.build_catalog()
    # add a new form; without clearing, cache returns the old tree
    (forms_tree / "ICS USA" / "ICS214.txt").write_text("Form: ICS214Input.html\nMsg:\nx\n")
    (forms_tree / "ICS USA" / "ICS214Input.html").write_text("<form></form>")
    t2 = catalog_mod.build_catalog()
    assert t1 == t2  # cached
    catalog_mod.clear_catalog_cache()
    t3 = catalog_mod.build_catalog()
    names = [f["name"] for f in {f["name"]: f for f in t3["folders"]}["ICS USA"]["forms"]]
    assert set(names) == {"ICS213", "ICS214"}


def test_catalog_cache_keyed_by_version(forms_tree):
    """Version change bypasses cache without needing clear_catalog_cache()."""
    catalog_mod.clear_catalog_cache()
    # Build with v1, should see ICS213 only
    t1 = catalog_mod.build_catalog(version="v1")
    names1 = [f["name"] for f in {f["name"]: f for f in t1["folders"]}["ICS USA"]["forms"]]
    assert names1 == ["ICS213"]

    # Add a new form to disk
    (forms_tree / "ICS USA" / "ICS214.txt").write_text("Form: ICS214Input.html\nMsg:\nx\n")
    (forms_tree / "ICS USA" / "ICS214Input.html").write_text("<form></form>")

    # Build with v2 (different version), should see both even without clear
    t2 = catalog_mod.build_catalog(version="v2")
    names2 = [f["name"] for f in {f["name"]: f for f in t2["folders"]}["ICS USA"]["forms"]]
    assert set(names2) == {"ICS213", "ICS214"}

    # v2 was cached, so building again with v2 should return cached tree
    t2_again = catalog_mod.build_catalog(version="v2")
    assert t2 == t2_again


# Route tests (ASGI client, net member, patch catalog.forms_library_dir)

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
        user = User(
            callsign="KD0TST",
            oidc_subject="auth0|member",
            name="Test Member",
        )
        net = Net(slug="t", name="Test Net")
        session.add_all([user, net])
        session.commit()
        session.refresh(user)
        session.refresh(net)
        membership = NetMembership(
            user_callsign=user.callsign,
            net_id=net.id,
            role=NetRole.VIEWER,
        )
        session.add(membership)
        session.commit()
    return factory


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
async def test_catalog_route(test_client, test_settings, tmp_path, monkeypatch):
    """GET /api/nets/{slug}/forms/catalog returns the catalog tree (net member auth)."""
    # Patch forms library to point to our test directory
    base = tmp_path / "forms"
    ics = base / "ICS USA"
    ics.mkdir(parents=True)
    (ics / "ICS213.txt").write_text("Form: ICS213Input.html\nMsg:\nx\n")
    (ics / "ICS213Input.html").write_text("<form></form>")
    monkeypatch.setattr(catalog_mod, "forms_library_dir", lambda: base)
    catalog_mod.clear_catalog_cache()

    # Mint a token for the test member
    token = make_test_token("KD0TST", test_settings, token_version=0)
    headers = {"cookie": f"access_token={token}"}

    resp = await test_client.get("/api/nets/t/forms/catalog", headers=headers)
    assert resp.status_code == 200
    tree = resp.json()
    folders = {f["name"]: f for f in tree["folders"]}
    assert "ICS USA" in folders
    assert len(folders["ICS USA"]["forms"]) == 1
    assert folders["ICS USA"]["forms"][0]["name"] == "ICS213"


@pytest.mark.asyncio
async def test_catalog_route_filter(test_client, test_settings, tmp_path, monkeypatch):
    """GET /api/nets/{slug}/forms/catalog?q=xyz filters by form name (case-insensitive)."""
    base = tmp_path / "forms"
    ics = base / "ICS USA"
    ics.mkdir(parents=True)
    (ics / "ICS213.txt").write_text("Form: ICS213Input.html\nMsg:\nx\n")
    (ics / "ICS213Input.html").write_text("<form></form>")
    (ics / "ICS214.txt").write_text("Form: ICS214Input.html\nMsg:\nx\n")
    (ics / "ICS214Input.html").write_text("<form></form>")
    monkeypatch.setattr(catalog_mod, "forms_library_dir", lambda: base)
    catalog_mod.clear_catalog_cache()

    token = make_test_token("KD0TST", test_settings, token_version=0)
    headers = {"cookie": f"access_token={token}"}

    # Filter for ICS213
    resp = await test_client.get("/api/nets/t/forms/catalog?q=ics213", headers=headers)
    assert resp.status_code == 200
    tree = resp.json()
    folders = {f["name"]: f for f in tree["folders"]}
    assert "ICS USA" in folders
    forms = folders["ICS USA"]["forms"]
    assert len(forms) == 1
    assert forms[0]["name"] == "ICS213"
