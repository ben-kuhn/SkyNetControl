import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User
from backend.auth.pat_models import PersonalAccessToken
from backend.auth.pat_service import (
    create_token,
    list_tokens,
    revoke_token,
    authenticate_token,
)



@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite:///",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session_factory(db_engine):
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture
def seeded_db(db_session_factory):
    with db_session_factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin User",
            is_admin=True,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer User",
        )
        session.add_all([admin, viewer])
        session.commit()
    return db_session_factory


def test_personal_access_token_model_creation(seeded_db):
    with seeded_db() as session:
        token = PersonalAccessToken(
            user_callsign="W0NE",
            name="Test token",
            token_hash="a" * 64,
            token_prefix="skynet_a",
            scopes="schedule:read,checkins:read",
        )
        session.add(token)
        session.commit()
        session.refresh(token)

        assert token.id is not None
        assert token.user_callsign == "W0NE"
        assert token.name == "Test token"
        assert token.token_hash == "a" * 64
        assert token.token_prefix == "skynet_a"
        assert token.scopes == "schedule:read,checkins:read"
        assert token.created_at is not None
        assert token.expires_at is None
        assert token.last_used_at is None
        assert token.revoked_at is None


def test_create_token_returns_raw_token(seeded_db):
    with seeded_db() as session:
        result = create_token(
            db=session,
            user_callsign="W0NE",
            is_admin=True,
            name="My token",
            scopes=["schedule:read"],
            expires_at=None,
        )
        assert result["token"].startswith("skynet_")
        assert len(result["token"]) == 71  # "skynet_" (7) + 64 hex chars
        assert result["name"] == "My token"
        assert result["token_prefix"] == result["token"][:8]
        assert result["scopes"] == ["schedule:read"]
        assert result["id"] is not None


def test_create_token_stores_hash_not_raw(seeded_db):
    with seeded_db() as session:
        result = create_token(
            db=session,
            user_callsign="W0NE",
            is_admin=True,
            name="Hash test",
            scopes=["schedule:read"],
            expires_at=None,
        )
        raw = result["token"]
        pat = session.query(PersonalAccessToken).filter_by(id=result["id"]).one()
        assert pat.token_hash != raw
        assert len(pat.token_hash) == 64  # SHA-256 hex


def test_create_token_rejects_admin_scope_for_non_admin(seeded_db):
    """Non-admin users cannot create tokens with admin-only scopes."""
    with seeded_db() as session:
        with pytest.raises(ValueError, match="Only admins"):
            create_token(
                db=session,
                user_callsign="KD0TST",
                is_admin=False,
                name="Bad scope",
                scopes=["users:write"],
                expires_at=None,
            )


def test_create_token_enforces_max_10(seeded_db):
    with seeded_db() as session:
        for i in range(10):
            create_token(
                db=session,
                user_callsign="W0NE",
                is_admin=True,
                name=f"Token {i}",
                scopes=["schedule:read"],
                expires_at=None,
            )
        with pytest.raises(ValueError, match="maximum"):
            create_token(
                db=session,
                user_callsign="W0NE",
                is_admin=True,
                name="Token 11",
                scopes=["schedule:read"],
                expires_at=None,
            )


def test_create_token_rejects_past_expiry(seeded_db):
    with seeded_db() as session:
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="future"):
            create_token(
                db=session,
                user_callsign="W0NE",
                is_admin=True,
                name="Expired",
                scopes=["schedule:read"],
                expires_at=past,
            )


def test_create_token_rejects_empty_name(seeded_db):
    with seeded_db() as session:
        with pytest.raises(ValueError, match="name"):
            create_token(
                db=session,
                user_callsign="W0NE",
                is_admin=True,
                name="",
                scopes=["schedule:read"],
                expires_at=None,
            )


def test_create_token_rejects_long_name(seeded_db):
    with seeded_db() as session:
        with pytest.raises(ValueError, match="name"):
            create_token(
                db=session,
                user_callsign="W0NE",
                is_admin=True,
                name="x" * 101,
                scopes=["schedule:read"],
                expires_at=None,
            )


def test_list_tokens_returns_only_own(seeded_db):
    with seeded_db() as session:
        create_token(session, "W0NE", True, "Admin token", ["schedule:read"], None)
        create_token(session, "KD0TST", False, "Viewer token", ["schedule:read"], None)
        tokens = list_tokens(session, "W0NE")
        assert len(tokens) == 1
        assert tokens[0]["name"] == "Admin token"
        assert "token" not in tokens[0]  # no raw token


def test_list_tokens_excludes_revoked(seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", True, "Revoked", ["schedule:read"], None)
        revoke_token(session, result["id"], "W0NE", is_admin=True)
        tokens = list_tokens(session, "W0NE")
        assert len(tokens) == 0


def test_revoke_token_sets_revoked_at(seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", True, "To revoke", ["schedule:read"], None)
        revoke_token(session, result["id"], "W0NE", is_admin=False)
        pat = session.query(PersonalAccessToken).filter_by(id=result["id"]).one()
        assert pat.revoked_at is not None


def test_revoke_token_admin_can_revoke_others(seeded_db):
    with seeded_db() as session:
        result = create_token(session, "KD0TST", False, "Viewer token", ["schedule:read"], None)
        revoke_token(session, result["id"], "W0NE", is_admin=True)
        pat = session.query(PersonalAccessToken).filter_by(id=result["id"]).one()
        assert pat.revoked_at is not None


def test_revoke_token_non_owner_non_admin_fails(seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", True, "Admin token", ["schedule:read"], None)
        with pytest.raises(ValueError, match="not found"):
            revoke_token(session, result["id"], "KD0TST", is_admin=False)


def test_authenticate_valid_token(seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", True, "Auth test", ["schedule:read"], None)
        raw = result["token"]
        auth = authenticate_token(session, raw)
        assert auth is not None
        assert auth["user_callsign"] == "W0NE"
        assert auth["scopes"] == ["schedule:read"]


def test_authenticate_revoked_token_fails(seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", True, "Revoked", ["schedule:read"], None)
        raw = result["token"]
        revoke_token(session, result["id"], "W0NE", is_admin=False)
        auth = authenticate_token(session, raw)
        assert auth is None


def test_authenticate_expired_token_fails(seeded_db):
    with seeded_db() as session:
        future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        result = create_token(session, "W0NE", True, "Expired", ["schedule:read"], future)
        raw = result["token"]
        pat = session.query(PersonalAccessToken).filter_by(id=result["id"]).one()
        pat.expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        session.commit()
        auth = authenticate_token(session, raw)
        assert auth is None


def test_authenticate_invalid_token_fails(seeded_db):
    with seeded_db() as session:
        auth = authenticate_token(session, "skynet_" + "f" * 64)
        assert auth is None


def test_create_token_max_excludes_expired(seeded_db):
    """Expired tokens don't count toward the 10-token limit."""
    with seeded_db() as session:
        for i in range(10):
            result = create_token(
                db=session,
                user_callsign="W0NE",
                is_admin=True,
                name=f"Token {i}",
                scopes=["schedule:read"],
                expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )
        # All 10 are active and not expired — should fail
        with pytest.raises(ValueError, match="maximum"):
            create_token(session, "W0NE", True, "Too many", ["schedule:read"], None)

        # Now expire all of them
        for pat in session.query(PersonalAccessToken).filter_by(user_callsign="W0NE").all():
            pat.expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        session.commit()

        # Should succeed now since all are expired
        result = create_token(session, "W0NE", True, "New token", ["schedule:read"], None)
        assert result["id"] is not None


def test_authenticate_updates_last_used_at(seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", True, "Usage test", ["schedule:read"], None)
        raw = result["token"]
        pat = session.query(PersonalAccessToken).filter_by(id=result["id"]).one()
        assert pat.last_used_at is None
        authenticate_token(session, raw)
        session.refresh(pat)
        assert pat.last_used_at is not None
