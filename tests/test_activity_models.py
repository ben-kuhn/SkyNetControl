import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
import backend.modules.schedule.models
from backend.modules.activities.models import (
    Activity,
    ActivityTag,
    ActivityTagAssignment,
    ActivityUsage,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_create_activity(db: Session):
    activity = Activity(
        title="Simplex HF Net Exercise",
        description="Practice simplex HF communications",
        instructions="# Instructions\n\nTune to 7.185 MHz...",
        is_default=False,
    )
    db.add(activity)
    db.commit()

    fetched = db.get(Activity, activity.id)
    assert fetched is not None
    assert fetched.title == "Simplex HF Net Exercise"
    assert fetched.is_default is False
    assert fetched.created_at is not None


def test_default_activity(db: Session):
    activity = Activity(
        title="Standard Winlink Check-in",
        description="Default check-in activity",
        instructions="Send a one-line check-in or use the Winlink net check-in form.",
        is_default=True,
    )
    db.add(activity)
    db.commit()

    fetched = db.get(Activity, activity.id)
    assert fetched is not None
    assert fetched.is_default is True


def test_create_tag(db: Session):
    tag = ActivityTag(name="HF")
    db.add(tag)
    db.commit()

    fetched = db.get(ActivityTag, tag.id)
    assert fetched is not None
    assert fetched.name == "HF"


def test_tag_name_is_unique(db: Session):
    tag1 = ActivityTag(name="HF")
    tag2 = ActivityTag(name="HF")
    db.add(tag1)
    db.commit()
    db.add(tag2)
    with pytest.raises(Exception):
        db.commit()


def test_activity_tag_assignment(db: Session):
    activity = Activity(
        title="Test Activity",
        description="Test",
        instructions="Test instructions",
    )
    tag = ActivityTag(name="beginner-friendly")
    db.add_all([activity, tag])
    db.commit()

    assignment = ActivityTagAssignment(
        activity_id=activity.id, tag_id=tag.id
    )
    db.add(assignment)
    db.commit()

    db.refresh(activity)
    assert len(activity.tags) == 1
    assert activity.tags[0].name == "beginner-friendly"


def test_activity_usage(db: Session):
    activity = Activity(
        title="Test Activity",
        description="Test",
        instructions="Test instructions",
    )
    db.add(activity)
    db.commit()

    usage = ActivityUsage(
        activity_id=activity.id,
        session_id=1,
    )
    db.add(usage)
    db.commit()

    fetched = db.get(ActivityUsage, usage.id)
    assert fetched is not None
    assert fetched.activity_id == activity.id
    assert fetched.session_id == 1
    assert fetched.used_at is not None
