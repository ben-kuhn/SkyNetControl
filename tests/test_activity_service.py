import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.activities.service import (
    create_activity,
    delete_activity,
    get_activity,
    get_or_create_tags,
    list_activities,
    list_tags,
    update_activity,
)
from tests.conftest import make_test_net


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def net_id(db: Session) -> int:
    return make_test_net(db).id


def test_create_activity(db: Session, net_id: int):
    activity = create_activity(
        db,
        net_id=net_id,
        title="Test Activity",
        description="A test",
        instructions="Do the thing",
        tag_names=["HF", "beginner-friendly"],
    )
    assert activity.id is not None
    assert activity.net_id == net_id
    assert activity.title == "Test Activity"
    assert len(activity.tags) == 2
    tag_names = {t.name for t in activity.tags}
    assert tag_names == {"HF", "beginner-friendly"}


def test_create_activity_no_tags(db: Session, net_id: int):
    activity = create_activity(
        db,
        net_id=net_id,
        title="Simple Activity",
        description="Simple",
        instructions="Just check in",
    )
    assert activity.id is not None
    assert len(activity.tags) == 0


def test_list_activities(db: Session, net_id: int):
    create_activity(db, net_id=net_id, title="A1", description="d", instructions="i")
    create_activity(db, net_id=net_id, title="A2", description="d", instructions="i")
    activities = list_activities(db, net_id=net_id)
    assert len(activities) == 2


def test_list_activities_cross_net_isolation(db: Session, net_id: int):
    """Activities in net A are not visible through net B's list."""
    net2 = make_test_net(db, slug="net2", name="Net 2")
    create_activity(db, net_id=net_id, title="Net1 Only", description="d", instructions="i")
    create_activity(db, net_id=net2.id, title="Net2 Only", description="d", instructions="i")

    net1_activities = list_activities(db, net_id=net_id)
    net2_activities = list_activities(db, net_id=net2.id)

    assert len(net1_activities) == 1
    assert net1_activities[0].title == "Net1 Only"
    assert len(net2_activities) == 1
    assert net2_activities[0].title == "Net2 Only"


def test_get_activity(db: Session, net_id: int):
    created = create_activity(db, net_id=net_id, title="Find Me", description="d", instructions="i")
    found = get_activity(db, created.id, net_id=net_id)
    assert found is not None
    assert found.title == "Find Me"


def test_get_activity_not_found(db: Session, net_id: int):
    found = get_activity(db, 999, net_id=net_id)
    assert found is None


def test_get_activity_cross_net_returns_none(db: Session, net_id: int):
    """get_activity with wrong net_id returns None (cross-net isolation)."""
    net2 = make_test_net(db, slug="net2", name="Net 2")
    activity = create_activity(db, net_id=net_id, title="Net1 Only", description="d", instructions="i")

    assert get_activity(db, activity.id, net_id=net_id) is not None
    assert get_activity(db, activity.id, net_id=net2.id) is None


def test_update_activity(db: Session, net_id: int):
    activity = create_activity(db, net_id=net_id, title="Old Title", description="old", instructions="old")
    updated = update_activity(
        db,
        activity.id,
        net_id=net_id,
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


def test_update_activity_cross_net_returns_none(db: Session, net_id: int):
    """update_activity with wrong net_id returns None (cross-net protection)."""
    net2 = make_test_net(db, slug="net2", name="Net 2")
    activity = create_activity(db, net_id=net_id, title="Net1 Only", description="d", instructions="i")

    result = update_activity(db, activity.id, net_id=net2.id, title="Hacked")
    assert result is None

    # Original is untouched
    original = get_activity(db, activity.id, net_id=net_id)
    assert original is not None
    assert original.title == "Net1 Only"


def test_delete_activity(db: Session, net_id: int):
    activity = create_activity(db, net_id=net_id, title="Delete Me", description="d", instructions="i")
    result = delete_activity(db, activity.id, net_id=net_id)
    assert result is True
    assert get_activity(db, activity.id, net_id=net_id) is None


def test_cannot_delete_default_activity(db: Session, net_id: int):
    activity = create_activity(db, net_id=net_id, title="Default", description="d", instructions="i", is_default=True)
    result = delete_activity(db, activity.id, net_id=net_id)
    assert result is False
    assert get_activity(db, activity.id, net_id=net_id) is not None


def test_delete_activity_cross_net_returns_false(db: Session, net_id: int):
    """delete_activity with wrong net_id returns False (cross-net protection)."""
    net2 = make_test_net(db, slug="net2", name="Net 2")
    activity = create_activity(db, net_id=net_id, title="Net1 Only", description="d", instructions="i")

    result = delete_activity(db, activity.id, net_id=net2.id)
    assert result is False

    # Activity still exists in net1
    assert get_activity(db, activity.id, net_id=net_id) is not None


def test_is_default_per_net(db: Session, net_id: int):
    """Setting is_default in net A does not affect net B's default."""
    net2 = make_test_net(db, slug="net2", name="Net 2")

    a1 = create_activity(db, net_id=net_id, title="Net1 Default", description="d", instructions="i", is_default=True)
    a2 = create_activity(db, net_id=net2.id, title="Net2 Default", description="d", instructions="i", is_default=True)

    # Both nets have their own default; they don't interfere
    assert get_activity(db, a1.id, net_id=net_id).is_default is True
    assert get_activity(db, a2.id, net_id=net2.id).is_default is True

    # Create another default in net1 — should clear a1 but NOT a2
    a1b = create_activity(
        db, net_id=net_id, title="Net1 New Default", description="d", instructions="i", is_default=True
    )
    db.refresh(a1)
    db.refresh(a2)
    db.refresh(a1b)

    assert a1.is_default is False  # replaced
    assert a1b.is_default is True  # new net1 default
    assert a2.is_default is True  # net2 default unchanged


def test_get_or_create_tags_reuses_existing(db: Session, net_id: int):
    from backend.modules.activities.models import ActivityTag

    tag = ActivityTag(net_id=net_id, name="HF")
    db.add(tag)
    db.commit()

    tags = get_or_create_tags(db, net_id, ["HF", "VHF"])
    assert len(tags) == 2
    hf_tag = next(t for t in tags if t.name == "HF")
    assert hf_tag.id == tag.id  # reused, not duplicated


def test_get_or_create_tags_per_net(db: Session, net_id: int):
    """Tags from different nets are separate even with the same name."""
    net2 = make_test_net(db, slug="net2", name="Net 2")

    tags_net1 = get_or_create_tags(db, net_id, ["checkin"])
    tags_net2 = get_or_create_tags(db, net2.id, ["checkin"])

    assert len(tags_net1) == 1
    assert len(tags_net2) == 1
    assert tags_net1[0].id != tags_net2[0].id
    assert tags_net1[0].net_id == net_id
    assert tags_net2[0].net_id == net2.id


def test_list_tags(db: Session, net_id: int):
    net2 = make_test_net(db, slug="net2", name="Net 2")
    get_or_create_tags(db, net_id, ["HF", "VHF"])
    get_or_create_tags(db, net2.id, ["SHF"])

    net1_tags = list_tags(db, net_id=net_id)
    net2_tags = list_tags(db, net_id=net2.id)

    assert {t.name for t in net1_tags} == {"HF", "VHF"}
    assert {t.name for t in net2_tags} == {"SHF"}
