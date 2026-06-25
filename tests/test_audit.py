import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.auth.service import create_access_token
from backend.audit.models import AuditLog
from backend.audit.service import log_action
from backend.audit.routes import audit_router
from backend.config import Settings



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
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            is_admin=True,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
            
        )
        session.add_all([admin, viewer])
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(audit_router, prefix="/api/audit")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- Service tests ---


def test_log_action_creates_entry(db_setup):
    with db_setup() as db:
        log_action(
            db, actor="W0NE", action="user.role_changed", target="KD0TST", details={"from": "pending", "to": "viewer"}
        )
        entries = db.query(AuditLog).all()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.actor_callsign == "W0NE"
        assert entry.action == "user.role_changed"
        assert entry.target_callsign == "KD0TST"
        parsed = json.loads(entry.details)
        assert parsed == {"from": "pending", "to": "viewer"}
        assert entry.created_at is not None


def test_log_action_without_target_or_details(db_setup):
    with db_setup() as db:
        log_action(db, actor="W0NE", action="config.updated")
        entry = db.query(AuditLog).one()
        assert entry.target_callsign is None
        assert entry.details is None


def test_log_action_multiple_entries_ordered(db_setup):
    with db_setup() as db:
        log_action(db, actor="W0NE", action="first")
        log_action(db, actor="W0NE", action="second")
        log_action(db, actor="W0NE", action="third")
        entries = db.query(AuditLog).order_by(AuditLog.id.desc()).all()
        assert [e.action for e in entries] == ["third", "second", "first"]


# --- Route tests ---


@pytest.mark.asyncio
async def test_admin_can_list_audit_log(test_client, test_settings, db_setup):
    with db_setup() as db:
        log_action(
            db, actor="W0NE", action="user.role_changed", target="KD0TST", details={"from": "pending", "to": "viewer"}
        )

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.get("/api/audit/", cookies={"access_token": token})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["actor_callsign"] == "W0NE"
    assert data[0]["action"] == "user.role_changed"
    assert data[0]["target_callsign"] == "KD0TST"
    assert data[0]["details"] == {"from": "pending", "to": "viewer"}
    assert "created_at" in data[0]


@pytest.mark.asyncio
async def test_audit_log_respects_limit(test_client, test_settings, db_setup):
    with db_setup() as db:
        for i in range(5):
            log_action(db, actor="W0NE", action=f"action_{i}")

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.get("/api/audit/?limit=3", cookies={"access_token": token})
    assert response.status_code == 200
    assert len(response.json()) == 3


@pytest.mark.asyncio
async def test_audit_log_newest_first(test_client, test_settings, db_setup):
    with db_setup() as db:
        log_action(db, actor="W0NE", action="first")
        log_action(db, actor="W0NE", action="second")

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.get("/api/audit/", cookies={"access_token": token})
    actions = [e["action"] for e in response.json()]
    assert actions == ["second", "first"]


@pytest.mark.asyncio
async def test_viewer_cannot_list_audit_log(test_client, test_settings):
    token = make_test_token("KD0TST", test_settings, token_version=0)
    response = await test_client.get("/api/audit/", cookies={"access_token": token})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_list_audit_log(test_client):
    response = await test_client.get("/api/audit/")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_audit_log_limit_capped_at_200(test_client, test_settings):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    response = await test_client.get("/api/audit/?limit=500", cookies={"access_token": token})
    # Query validation rejects limit > 200
    assert response.status_code == 422


# --- Wiring tests ---

from backend.auth.routes import auth_router
from backend.config_mgmt.routes import config_router
from tests.conftest import make_test_token


@pytest.fixture
def wired_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(config_router, prefix="/api/config")
    app.include_router(audit_router, prefix="/api/audit")
    return app


@pytest.fixture
async def wired_client(wired_app):
    transport = ASGITransport(app=wired_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_role_change_creates_audit_entry(wired_client, test_settings, db_setup):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    await wired_client.patch(
        "/api/auth/users/KD0TST",
        json={"is_admin": True},
        cookies={"access_token": token},
    )

    response = await wired_client.get("/api/audit/", cookies={"access_token": token})
    entries = response.json()
    assert len(entries) == 1
    assert entries[0]["action"] == "user.role_changed"
    assert entries[0]["actor_callsign"] == "W0NE"
    assert entries[0]["target_callsign"] == "KD0TST"
    assert entries[0]["details"]["is_admin"] is True


@pytest.mark.asyncio
async def test_callsign_approve_creates_audit_entry(wired_client, test_settings, db_setup):
    with db_setup() as db:
        user = db.get(User, "KD0TST")
        user.pending_callsign = "KD0NEW"
        db.commit()

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    await wired_client.post(
        "/api/auth/users/KD0TST/approve-callsign",
        cookies={"access_token": token},
    )

    response = await wired_client.get("/api/audit/", cookies={"access_token": token})
    entries = response.json()
    assert len(entries) == 1
    assert entries[0]["action"] == "user.callsign_approved"
    assert entries[0]["target_callsign"] == "KD0NEW"
    assert entries[0]["details"]["old"] == "KD0TST"
    assert entries[0]["details"]["new"] == "KD0NEW"


@pytest.mark.asyncio
async def test_callsign_reject_creates_audit_entry(wired_client, test_settings, db_setup):
    with db_setup() as db:
        user = db.get(User, "KD0TST")
        user.pending_callsign = "KD0NEW"
        db.commit()

    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    await wired_client.delete(
        "/api/auth/users/KD0TST/pending-callsign",
        cookies={"access_token": token},
    )

    response = await wired_client.get("/api/audit/", cookies={"access_token": token})
    entries = response.json()
    assert len(entries) == 1
    assert entries[0]["action"] == "user.callsign_rejected"
    assert entries[0]["target_callsign"] == "KD0TST"
    assert entries[0]["details"]["pending"] == "KD0NEW"


@pytest.mark.asyncio
async def test_config_update_creates_audit_entry(wired_client, test_settings, db_setup):
    token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    await wired_client.put(
        "/api/config/net_address",
        json={"value": "w0ne@winlink.org"},
        cookies={"access_token": token},
    )

    response = await wired_client.get("/api/audit/", cookies={"access_token": token})
    entries = response.json()
    assert len(entries) == 1
    assert entries[0]["action"] == "config.updated"
    assert entries[0]["actor_callsign"] == "W0NE"
    assert entries[0]["target_callsign"] is None
    assert entries[0]["details"]["key"] == "net_address"
    assert entries[0]["details"]["value"] == "w0ne@winlink.org"
