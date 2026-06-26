import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.modules.activities.models import Activity, ChatSession
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.config import Settings
from tests.conftest import make_test_token

NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/activities"


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
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            is_admin=True,
        )
        net_control = User(
            callsign="W0NC",
            oidc_subject="auth0|netcontrol",
            name="Net Control",
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
        )
        outsider = User(
            callsign="W0OUT",
            oidc_subject="auth0|outsider",
            name="Outsider",
        )
        net = Net(slug=NET_SLUG, name="Test Net")
        session.add_all([admin, net_control, viewer, outsider, net])
        session.flush()

        # Viewer membership
        session.add(NetMembership(user_callsign="KD0TST", net_id=net.id, role=NetRole.VIEWER))
        # Net control membership
        session.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        # outsider has no membership

        session.commit()
        yield {
            "engine": engine,
            "factory": factory,
            "admin": admin,
            "net_control": net_control,
            "viewer": viewer,
            "outsider": outsider,
            "net": net,
        }
    engine.dispose()


@pytest.fixture
def app(test_settings, db_setup):
    from backend.app import create_app

    application = create_app(settings=test_settings)
    application.state.engine = db_setup["engine"]
    application.state.session_factory = db_setup["factory"]
    Base.metadata.create_all(db_setup["engine"])
    return application


@pytest.fixture
async def test_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin_client(app, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token}) as c:
        yield c


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


# --- CRUD ---


@pytest.mark.asyncio
async def test_create_activity(admin_client, db_setup):
    resp = await admin_client.post(
        BASE + "/",
        json={
            "title": "Simplex Exercise",
            "description": "Practice simplex",
            "instructions": "Tune to 7.185 MHz",
            "tag_names": ["HF", "beginner-friendly"],
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Simplex Exercise"
    assert data["id"] is not None
    assert data["net_id"] == db_setup["net"].id
    assert len(data["tags"]) == 2


@pytest.mark.asyncio
async def test_list_activities(admin_client, db_setup):
    await admin_client.post(
        BASE + "/",
        json={"title": "Activity 1", "description": "d", "instructions": "i"},
    )
    resp = await admin_client.get(BASE + "/")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_get_activity(admin_client):
    create_resp = await admin_client.post(
        BASE + "/",
        json={"title": "Find Me", "description": "d", "instructions": "i"},
    )
    activity_id = create_resp.json()["id"]
    resp = await admin_client.get(f"{BASE}/{activity_id}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Find Me"


@pytest.mark.asyncio
async def test_update_activity(admin_client):
    create_resp = await admin_client.post(
        BASE + "/",
        json={"title": "Old", "description": "old", "instructions": "old"},
    )
    activity_id = create_resp.json()["id"]
    resp = await admin_client.patch(f"{BASE}/{activity_id}", json={"title": "New", "tag_names": ["VHF"]})
    assert resp.status_code == 200
    assert resp.json()["title"] == "New"
    assert len(resp.json()["tags"]) == 1


@pytest.mark.asyncio
async def test_delete_activity(admin_client):
    create_resp = await admin_client.post(
        BASE + "/",
        json={"title": "Delete Me", "description": "d", "instructions": "i"},
    )
    activity_id = create_resp.json()["id"]
    resp = await admin_client.delete(f"{BASE}/{activity_id}")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_cannot_delete_default_activity(admin_client, db_setup):
    with db_setup["factory"]() as session:
        activity = Activity(
            net_id=db_setup["net"].id,
            title="Default",
            description="Default check-in",
            instructions="Check in",
            is_default=True,
        )
        session.add(activity)
        session.commit()
        activity_id = activity.id

    resp = await admin_client.delete(f"{BASE}/{activity_id}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_tags(admin_client):
    await admin_client.post(
        BASE + "/",
        json={"title": "Tagged", "description": "d", "instructions": "i", "tag_names": ["HF", "VHF"]},
    )
    resp = await admin_client.get(BASE + "/tags")
    assert resp.status_code == 200
    tags = resp.json()
    assert len(tags) == 2


# --- Role gating ---


@pytest.mark.asyncio
async def test_anonymous_gets_401(test_client):
    resp = await test_client.get(BASE + "/")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_outsider_gets_403(test_client, test_settings):
    token = make_test_token("W0OUT", test_settings)
    test_client.cookies.set("access_token", token)
    resp = await test_client.get(BASE + "/")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_read(viewer_client, admin_client):
    # Create as admin
    await admin_client.post(
        BASE + "/",
        json={"title": "Activity", "description": "d", "instructions": "i"},
    )
    # Viewer can list
    resp = await viewer_client.get(BASE + "/")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_create(viewer_client):
    resp = await viewer_client.post(
        BASE + "/",
        json={"title": "Hack", "description": "d", "instructions": "i"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_update(admin_client, viewer_client):
    create_resp = await admin_client.post(
        BASE + "/",
        json={"title": "Original", "description": "d", "instructions": "i"},
    )
    activity_id = create_resp.json()["id"]
    resp = await viewer_client.patch(f"{BASE}/{activity_id}", json={"title": "Hacked"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_delete(admin_client, viewer_client):
    create_resp = await admin_client.post(
        BASE + "/",
        json={"title": "Protected", "description": "d", "instructions": "i"},
    )
    activity_id = create_resp.json()["id"]
    resp = await viewer_client.delete(f"{BASE}/{activity_id}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_net_control_can_create_and_update(nc_client):
    resp = await nc_client.post(
        BASE + "/",
        json={"title": "NC Activity", "description": "d", "instructions": "i"},
    )
    assert resp.status_code == 201
    activity_id = resp.json()["id"]

    resp = await nc_client.patch(f"{BASE}/{activity_id}", json={"title": "NC Updated"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "NC Updated"


# --- Cross-net isolation ---


@pytest.mark.asyncio
async def test_cross_net_get_returns_404(admin_client, db_setup):
    """GET an activity from net A via net B's slug returns 404."""
    net2_slug = "net2"
    with db_setup["factory"]() as session:
        net2 = Net(slug=net2_slug, name="Net 2")
        session.add(net2)
        session.flush()

        activity = Activity(
            net_id=net2.id,
            title="Net2 Activity",
            description="d",
            instructions="i",
        )
        session.add(activity)
        session.commit()
        activity_id = activity.id

    # Access via net1's slug → 404
    resp = await admin_client.get(f"{BASE}/{activity_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cross_net_update_returns_404(admin_client, db_setup):
    """PATCH an activity from net A via net B's slug returns 404."""
    net2_slug = "net2"
    with db_setup["factory"]() as session:
        net2 = Net(slug=net2_slug, name="Net 2")
        session.add(net2)
        session.flush()

        activity = Activity(
            net_id=net2.id,
            title="Net2 Activity",
            description="d",
            instructions="i",
        )
        session.add(activity)
        session.commit()
        activity_id = activity.id

    # Try to update net2's activity via net1's slug
    resp = await admin_client.patch(f"{BASE}/{activity_id}", json={"title": "Hacked"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cross_net_list_isolation(admin_client, db_setup):
    """List via net A's slug does not reveal net B's activities."""
    with db_setup["factory"]() as session:
        net2 = Net(slug="net2", name="Net 2")
        session.add(net2)
        session.flush()

        session.add(Activity(net_id=db_setup["net"].id, title="Net1 Activity", description="d", instructions="i"))
        session.add(Activity(net_id=net2.id, title="Net2 Activity", description="d", instructions="i"))
        session.commit()

    resp = await admin_client.get(BASE + "/")
    assert resp.status_code == 200
    titles = [a["title"] for a in resp.json()]
    assert "Net1 Activity" in titles
    assert "Net2 Activity" not in titles


# --- is_default per-net ---


@pytest.mark.asyncio
async def test_is_default_per_net(admin_client, db_setup):
    """Setting default in net A does not unset net B's default."""
    with db_setup["factory"]() as session:
        net2 = Net(slug="net2", name="Net 2")
        session.add(net2)
        session.flush()

        a_net1 = Activity(
            net_id=db_setup["net"].id, title="Net1 Default", description="d", instructions="i", is_default=True
        )
        a_net2 = Activity(
            net_id=net2.id, title="Net2 Default", description="d", instructions="i", is_default=True
        )
        session.add_all([a_net1, a_net2])
        session.commit()
        net2_default_id = a_net2.id

    # Create new default in net1 via API
    resp = await admin_client.post(
        BASE + "/",
        json={"title": "Net1 New Default", "description": "d", "instructions": "i"},
    )
    assert resp.status_code == 201

    # Net2's default must still be set
    with db_setup["factory"]() as session:
        a_net2_refetched = session.get(Activity, net2_default_id)
        assert a_net2_refetched.is_default is True


# --- Chat session cross-net isolation ---


@pytest.mark.asyncio
async def test_chat_session_cross_net_isolation(admin_client, db_setup, test_settings):
    """A chat session linked to net A's activity is not accessible via net B's slug."""
    with db_setup["factory"]() as session:
        net2 = Net(slug="net2", name="Net 2")
        session.add(net2)
        session.flush()

        # Create an activity in net2 and a chat session linked to it
        activity = Activity(net_id=net2.id, title="Net2 Activity", description="d", instructions="i")
        session.add(activity)
        session.flush()

        chat = ChatSession(activity_id=activity.id)
        session.add(chat)
        session.commit()
        chat_id = chat.id

    # Access via net1's slug → 404
    resp = await admin_client.get(f"{BASE}/chat/sessions/{chat_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_session_unlinked_accessible(admin_client, db_setup):
    """A chat session not yet linked to any activity is accessible within any net."""
    with db_setup["factory"]() as session:
        chat = ChatSession()
        session.add(chat)
        session.commit()
        chat_id = chat.id

    # Unlinked sessions are accessible (they inherit net when approved)
    resp = await admin_client.get(f"{BASE}/chat/sessions/{chat_id}")
    assert resp.status_code == 200
