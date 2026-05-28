from backend.auth.models import UserRole


def test_deleted_role_exists():
    assert UserRole.DELETED == "deleted"
    assert "deleted" in [r.value for r in UserRole]


import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.auth.dependencies import get_current_user
from backend.config import Settings


@pytest.fixture
def privacy_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )


@pytest.fixture
def privacy_db():
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
            role=UserRole.ADMIN,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
            role=UserRole.VIEWER,
            email="viewer@example.com",
        )
        deleted = User(
            callsign="ANON-AAAA",
            oidc_subject="deleted",
            name="Deleted User",
            role=UserRole.DELETED,
        )
        session.add_all([admin, viewer, deleted])
        session.commit()
    return factory


@pytest.fixture
def auth_app(privacy_settings, privacy_db):
    app = FastAPI()
    app.state.session_factory = privacy_db
    app.state.settings = privacy_settings

    @app.get("/me")
    async def me(user: User = Depends(get_current_user)):
        return {"callsign": user.callsign}

    return app


@pytest.fixture
async def auth_client(auth_app):
    transport = ASGITransport(app=auth_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_deleted_user_cannot_authenticate(auth_client, privacy_settings):
    token = create_access_token("ANON-AAAA", "deleted", privacy_settings)
    response = await auth_client.get("/me", cookies={"access_token": token})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_deleted_user_pat_bearer_returns_401(auth_client, privacy_db):
    from backend.auth.pat_models import PersonalAccessToken
    import hashlib, secrets
    raw = "skynet_" + secrets.token_hex(32)
    with privacy_db() as session:
        pat = PersonalAccessToken(
            user_callsign="ANON-AAAA",
            name="Deleted user token",
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            token_prefix=raw[:8],
            scopes="schedule:read",
        )
        session.add(pat)
        session.commit()
    response = await auth_client.get(
        "/me",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 401


from backend.modules.checkins.models import RawMessage, CheckIn, Member, MessageType, ParseStatus, TimingStatus
from backend.audit.models import AuditLog
from backend.auth.pat_models import PersonalAccessToken
from backend.privacy.service import anonymize_user

import hashlib
from datetime import datetime, timezone


@pytest.fixture
def rich_db():
    """DB with user data across all tables for anonymization testing."""
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
            role=UserRole.ADMIN,
        )
        target = User(
            callsign="KD0TST",
            oidc_subject="auth0|target",
            name="Test User",
            role=UserRole.VIEWER,
            email="test@example.com",
            pending_callsign="KD0NEW",
        )
        session.add_all([admin, target])
        session.flush()

        from backend.modules.schedule.models import NetSession
        net_session = NetSession(
            id=1,
            start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            grace_period_hours=1,
            session_type="regular",
            status="closed",
        )
        session.add(net_session)
        session.flush()

        raw_msg = RawMessage(
            message_id="msg-001",
            from_address="kd0tst@winlink.org",
            received_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
            subject="Check-in from KD0TST",
            body="Name: Test User\nCallsign: KD0TST",
            message_type=MessageType.FORM,
            parsed=True,
        )
        session.add(raw_msg)
        session.flush()

        checkin = CheckIn(
            session_id=1,
            raw_message_id=raw_msg.id,
            callsign="KD0TST",
            name="Test User",
            city="Denver",
            county="Denver",
            state="CO",
            mode="Winlink",
            comments="Good signal",
            latitude=39.7392,
            longitude=-104.9903,
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
        )
        session.add(checkin)

        member = Member(
            callsign="KD0TST",
            name="Test User",
            first_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_check_in_date=datetime(2026, 5, 20, tzinfo=timezone.utc),
            total_check_ins=10,
        )
        session.add(member)

        audit_entry = AuditLog(
            actor_callsign="W0NE",
            action="user.role_changed",
            target_callsign="KD0TST",
            details='{"from": "pending", "to": "viewer"}',
        )
        session.add(audit_entry)

        pat = PersonalAccessToken(
            user_callsign="KD0TST",
            name="Test Token",
            token_hash=hashlib.sha256(b"test").hexdigest(),
            token_prefix="skynet_t",
            scopes="schedule:read",
        )
        session.add(pat)

        session.commit()
    return factory


def test_anonymize_user_replaces_user_fields(rich_db):
    with rich_db() as db:
        result = anonymize_user(db, "KD0TST", actor_callsign="W0NE")

    anon_id = result["anonymous_id"]
    assert anon_id.startswith("ANON-")
    assert len(anon_id) == 9

    with rich_db() as db:
        assert db.get(User, "KD0TST") is None
        anon_user = db.get(User, anon_id)
        assert anon_user is not None
        assert anon_user.name == "Deleted User"
        assert anon_user.email is None
        assert anon_user.oidc_subject == f"deleted:{anon_id}"
        assert anon_user.pending_callsign is None
        assert anon_user.role == UserRole.DELETED


def test_anonymize_user_replaces_checkin_fields(rich_db):
    with rich_db() as db:
        result = anonymize_user(db, "KD0TST", actor_callsign="W0NE")
    anon_id = result["anonymous_id"]

    with rich_db() as db:
        checkins = db.query(CheckIn).filter(CheckIn.callsign == anon_id).all()
        assert len(checkins) == 1
        ci = checkins[0]
        assert ci.name == "Deleted User"
        assert ci.city is None
        assert ci.county is None
        assert ci.state is None
        assert ci.latitude is None
        assert ci.longitude is None
        assert ci.comments is None


def test_anonymize_user_redacts_raw_messages(rich_db):
    with rich_db() as db:
        anonymize_user(db, "KD0TST", actor_callsign="W0NE")

    with rich_db() as db:
        msgs = db.query(RawMessage).all()
        assert len(msgs) == 1
        assert msgs[0].from_address == "anonymized"
        assert msgs[0].subject == "[redacted]"
        assert msgs[0].body == "[redacted]"


def test_anonymize_user_replaces_member_record(rich_db):
    with rich_db() as db:
        result = anonymize_user(db, "KD0TST", actor_callsign="W0NE")
    anon_id = result["anonymous_id"]

    with rich_db() as db:
        assert db.get(Member, "KD0TST") is None
        anon_member = db.get(Member, anon_id)
        assert anon_member is not None
        assert anon_member.name == "Deleted User"


def test_anonymize_user_updates_audit_log(rich_db):
    with rich_db() as db:
        result = anonymize_user(db, "KD0TST", actor_callsign="W0NE")
    anon_id = result["anonymous_id"]

    with rich_db() as db:
        entries = db.query(AuditLog).order_by(AuditLog.id).all()
        assert entries[0].target_callsign == anon_id
        anon_entry = [e for e in entries if e.action == "user.anonymized"]
        assert len(anon_entry) == 1
        assert anon_entry[0].actor_callsign == "W0NE"
        assert anon_entry[0].target_callsign == anon_id


def test_anonymize_user_deletes_tokens(rich_db):
    with rich_db() as db:
        anonymize_user(db, "KD0TST", actor_callsign="W0NE")

    with rich_db() as db:
        tokens = db.query(PersonalAccessToken).all()
        assert len(tokens) == 0


def test_anonymize_admin_by_admin_blocked(rich_db):
    with rich_db() as db:
        with pytest.raises(ValueError, match="Cannot anonymize"):
            anonymize_user(db, "W0NE", actor_callsign="W0NE")


def test_anonymize_two_users_does_not_collide(rich_db):
    """Audit H2 regression: anonymizing a second user must not hit the
    users.oidc_subject unique constraint on a shared 'deleted' value."""
    # Seed an additional admin-promoted-to-admin so we can anonymize a viewer
    # and then a net_control without losing the sole admin.
    with rich_db() as db:
        db.add(User(
            callsign="W0SECOND",
            oidc_subject="auth0|second",
            name="Second User",
            role=UserRole.NET_CONTROL,
        ))
        db.commit()

    with rich_db() as db:
        first = anonymize_user(db, "KD0TST", actor_callsign="W0NE")

    with rich_db() as db:
        # Second anonymization used to fail with IntegrityError on the
        # shared oidc_subject="deleted" before the fix.
        second = anonymize_user(db, "W0SECOND", actor_callsign="W0NE")

    with rich_db() as db:
        a = db.get(User, first["anonymous_id"])
        b = db.get(User, second["anonymous_id"])
        assert a is not None
        assert b is not None
        assert a.oidc_subject != b.oidc_subject
        assert a.oidc_subject.startswith("deleted:")
        assert b.oidc_subject.startswith("deleted:")


def test_anonymize_sole_admin_self_blocked(rich_db):
    with rich_db() as db:
        with pytest.raises(ValueError, match="sole admin"):
            anonymize_user(db, "W0NE", actor_callsign="W0NE")


def test_anonymize_nonexistent_user(rich_db):
    with rich_db() as db:
        with pytest.raises(ValueError, match="User not found"):
            anonymize_user(db, "NOPE", actor_callsign="W0NE")


from backend.privacy.service import export_user_data


def test_export_user_data_structure(rich_db):
    with rich_db() as db:
        data = export_user_data(db, "KD0TST")

    assert "exported_at" in data
    assert data["user"]["callsign"] == "KD0TST"
    assert data["user"]["name"] == "Test User"
    assert data["user"]["email"] == "test@example.com"
    assert data["user"]["role"] == "viewer"
    assert "created_at" in data["user"]


def test_export_includes_checkins(rich_db):
    with rich_db() as db:
        data = export_user_data(db, "KD0TST")

    assert len(data["check_ins"]) == 1
    ci = data["check_ins"][0]
    assert ci["callsign"] == "KD0TST"
    assert ci["city"] == "Denver"
    assert ci["latitude"] == 39.7392


def test_export_includes_raw_messages(rich_db):
    with rich_db() as db:
        data = export_user_data(db, "KD0TST")

    assert len(data["raw_messages"]) == 1
    msg = data["raw_messages"][0]
    assert msg["from_address"] == "kd0tst@winlink.org"
    assert "body" in msg


def test_export_includes_member_record(rich_db):
    with rich_db() as db:
        data = export_user_data(db, "KD0TST")

    assert data["member_record"]["callsign"] == "KD0TST"
    assert data["member_record"]["total_check_ins"] == 10


def test_export_includes_audit_log(rich_db):
    with rich_db() as db:
        data = export_user_data(db, "KD0TST")

    assert len(data["audit_log"]) == 1
    assert data["audit_log"][0]["target_callsign"] == "KD0TST"


def test_export_includes_tokens_without_secrets(rich_db):
    with rich_db() as db:
        data = export_user_data(db, "KD0TST")

    assert len(data["tokens"]) == 1
    tok = data["tokens"][0]
    assert tok["name"] == "Test Token"
    assert tok["scopes"] == "schedule:read"
    assert "token_hash" not in tok
    assert "token_prefix" not in tok


def test_export_nonexistent_user(rich_db):
    with rich_db() as db:
        with pytest.raises(ValueError, match="User not found"):
            export_user_data(db, "NOPE")
