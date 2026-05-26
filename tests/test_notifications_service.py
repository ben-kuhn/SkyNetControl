import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.modules.notifications.models import Notification, NotificationKind


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_notification_model_loads(db):
    from backend.auth.models import User, UserRole
    user = User(callsign="W0NE", oidc_subject="x", name="X", role=UserRole.ADMIN)
    db.add(user)
    db.flush()

    from datetime import datetime, timezone
    n = Notification(
        recipient_callsign="W0NE",
        kind=NotificationKind.REMINDER_DRAFT,
        message="Test",
        link_url="/reminders",
        created_at=datetime.now(tz=timezone.utc),
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    assert n.id is not None
    assert n.read_at is None
