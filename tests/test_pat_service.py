import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.pat_models import PersonalAccessToken


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
            role=UserRole.ADMIN,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer User",
            role=UserRole.VIEWER,
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
            token_prefix="skynet_a3",
            scopes="schedule:read,checkins:read",
        )
        session.add(token)
        session.commit()
        session.refresh(token)

        assert token.id is not None
        assert token.user_callsign == "W0NE"
        assert token.name == "Test token"
        assert token.token_hash == "a" * 64
        assert token.token_prefix == "skynet_a3"
        assert token.scopes == "schedule:read,checkins:read"
        assert token.created_at is not None
        assert token.expires_at is None
        assert token.last_used_at is None
        assert token.revoked_at is None
