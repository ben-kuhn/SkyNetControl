import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.auth.models import User, UserRole


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        yield session
    engine.dispose()


def test_create_user(db: Session):
    user = User(
        callsign="W0NE",
        oidc_subject="auth0|12345",
        name="John Doe",
        role=UserRole.ADMIN,
    )
    db.add(user)
    db.commit()

    fetched = db.get(User, "W0NE")
    assert fetched is not None
    assert fetched.callsign == "W0NE"
    assert fetched.oidc_subject == "auth0|12345"
    assert fetched.name == "John Doe"
    assert fetched.role == UserRole.ADMIN


def test_callsign_is_primary_key(db: Session):
    user = User(
        callsign="KD0TEST",
        oidc_subject="auth0|99999",
        name="Test User",
        role=UserRole.VIEWER,
    )
    db.add(user)
    db.commit()

    fetched = db.get(User, "KD0TEST")
    assert fetched is not None


def test_user_role_defaults_to_viewer(db: Session):
    user = User(
        callsign="N0CALL",
        oidc_subject="auth0|11111",
        name="New User",
    )
    db.add(user)
    db.commit()

    fetched = db.get(User, "N0CALL")
    assert fetched is not None
    assert fetched.role == UserRole.VIEWER


def test_oidc_subject_is_unique(db: Session):
    user1 = User(callsign="W0AAA", oidc_subject="auth0|same", name="User 1")
    user2 = User(callsign="W0BBB", oidc_subject="auth0|same", name="User 2")
    db.add(user1)
    db.commit()
    db.add(user2)
    with pytest.raises(Exception):
        db.commit()


def test_pending_role_exists():
    assert UserRole.PENDING.value == "pending"


def test_user_email_nullable(app):
    with app.state.session_factory() as session:
        user = User(
            callsign="W0TST",
            oidc_subject="test|email",
            name="Test",
            role=UserRole.VIEWER,
            email="test@example.com",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.email == "test@example.com"


def test_user_email_defaults_none(app):
    with app.state.session_factory() as session:
        user = User(
            callsign="W0NOE",
            oidc_subject="test|noemail",
            name="No Email",
            role=UserRole.VIEWER,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.email is None


def test_user_pending_callsign(app):
    with app.state.session_factory() as session:
        user = User(
            callsign="W0OLD",
            oidc_subject="test|pending_cs",
            name="Pending CS",
            role=UserRole.VIEWER,
            pending_callsign="W0NEW",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.pending_callsign == "W0NEW"
