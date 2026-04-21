import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.modules.activities.models import ActivityTag
from backend.modules.activities.service import (
    create_activity,
    get_activity,
    list_activities,
    update_activity,
    delete_activity,
    get_or_create_tags,
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
    activity = create_activity(
        db,
        title="Test Activity",
        description="A test",
        instructions="Do the thing",
        tag_names=["HF", "beginner-friendly"],
    )
    assert activity.id is not None
    assert activity.title == "Test Activity"
    assert len(activity.tags) == 2
    tag_names = {t.name for t in activity.tags}
    assert tag_names == {"HF", "beginner-friendly"}


def test_create_activity_no_tags(db: Session):
    activity = create_activity(
        db,
        title="Simple Activity",
        description="Simple",
        instructions="Just check in",
    )
    assert activity.id is not None
    assert len(activity.tags) == 0


def test_list_activities(db: Session):
    create_activity(db, title="A1", description="d", instructions="i")
    create_activity(db, title="A2", description="d", instructions="i")
    activities = list_activities(db)
    assert len(activities) == 2


def test_get_activity(db: Session):
    created = create_activity(db, title="Find Me", description="d", instructions="i")
    found = get_activity(db, created.id)
    assert found is not None
    assert found.title == "Find Me"


def test_get_activity_not_found(db: Session):
    found = get_activity(db, 999)
    assert found is None


def test_update_activity(db: Session):
    activity = create_activity(db, title="Old Title", description="old", instructions="old")
    updated = update_activity(
        db,
        activity.id,
        title="New Title",
        description="new",
        tag_names=["VHF"],
    )
    assert updated is not None
    assert updated.title == "New Title"
    assert updated.description == "new"
    assert updated.instructions == "old"  # not updated
    assert len(updated.tags) == 1
    assert updated.tags[0].name == "VHF"


def test_delete_activity(db: Session):
    activity = create_activity(db, title="Delete Me", description="d", instructions="i")
    result = delete_activity(db, activity.id)
    assert result is True
    assert get_activity(db, activity.id) is None


def test_cannot_delete_default_activity(db: Session):
    activity = create_activity(db, title="Default", description="d", instructions="i", is_default=True)
    result = delete_activity(db, activity.id)
    assert result is False
    assert get_activity(db, activity.id) is not None


def test_get_or_create_tags_reuses_existing(db: Session):
    tag = ActivityTag(name="HF")
    db.add(tag)
    db.commit()

    tags = get_or_create_tags(db, ["HF", "VHF"])
    assert len(tags) == 2
    hf_tag = next(t for t in tags if t.name == "HF")
    assert hf_tag.id == tag.id  # reused, not duplicated
