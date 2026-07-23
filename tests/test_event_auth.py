import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import Settings
from backend.db.base import Base
from backend.auth.models import User
from backend.modules.events.models import Event, EventOperator, EventType, EventStatus
from backend.modules.events.event_auth import EventRole, EventContext, require_event_role
from backend.auth.dependencies import get_db_session
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
                User(callsign="OP", oidc_subject="x|op", name="P"),
                User(callsign="OTHER", oidc_subject="x|ot", name="T"),
                User(callsign="ADM", oidc_subject="x|a", name="A", is_admin=True),
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
        db.add(
            EventOperator(
                event_id=priv.id,
                callsign="OP",
                added_by="OWNER",
                added_at=__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc),
            )
        )
        db.commit()
        ids = {"priv": priv.id, "pub": pub.id}
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = settings

    @app.get("/ev/{event_id}/read")
    async def read(ctx: EventContext = Depends(require_event_role(EventRole.READ))):
        return {"control": ctx.is_control}

    @app.get("/ev/{event_id}/control")
    async def control(ctx: EventContext = Depends(require_event_role(EventRole.CONTROL))):
        return {"ok": True}

    return app, settings, ids


def _c(app, settings, callsign=None, **kw):
    cookies = {}
    if callsign:
        cookies["access_token"] = make_test_token(callsign, settings, **kw)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies)


@pytest.mark.asyncio
async def test_control_matrix(app_ctx):
    app, settings, ids = app_ctx
    p = ids["priv"]
    async with _c(app, settings, "OWNER") as c:
        assert (await c.get(f"/ev/{p}/control")).status_code == 200
    async with _c(app, settings, "OP") as c:
        assert (await c.get(f"/ev/{p}/control")).status_code == 200
    async with _c(app, settings, "ADM", is_admin=True) as c:
        assert (await c.get(f"/ev/{p}/control")).status_code == 200
    async with _c(app, settings, "OTHER") as c:
        assert (await c.get(f"/ev/{p}/control")).status_code == 403
    async with _c(app, settings) as c:
        assert (await c.get(f"/ev/{p}/control")).status_code == 401


@pytest.mark.asyncio
async def test_read_matrix(app_ctx):
    app, settings, ids = app_ctx
    priv, pub = ids["priv"], ids["pub"]
    # private: only control users read; authenticated non-control gets 403
    async with _c(app, settings, "OTHER") as c:
        assert (await c.get(f"/ev/{priv}/read")).status_code == 403
    # public: any authed user reads
    async with _c(app, settings, "OTHER") as c:
        assert (await c.get(f"/ev/{pub}/read")).status_code == 200
    # public: anonymous with correct token reads; wrong/absent token 404
    async with _c(app, settings) as c:
        assert (await c.get(f"/ev/{pub}/read?token=utok")).status_code == 200
    async with _c(app, settings) as c:
        assert (await c.get(f"/ev/{pub}/read?token=bad")).status_code == 404
    async with _c(app, settings) as c:
        assert (await c.get(f"/ev/{priv}/read?token=ptok")).status_code == 404
