import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.activities.models import (
    Activity,
    ActivityTag,
    ActivityTagAssignment,
    ActivityUsage,
)


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
        # Insert two nets for cross-net tests
        from backend.modules.nets.models import Net

        net1 = Net(slug="net1", name="Net 1")
        net2 = Net(slug="net2", name="Net 2")
        session.add_all([net1, net2])
        session.commit()
        yield session
    engine.dispose()


def _net_id(db: Session, slug: str) -> int:
    from backend.modules.nets.models import Net

    return db.query(Net).filter(Net.slug == slug).one().id


def test_create_activity(db: Session):
    net_id = _net_id(db, "net1")
    activity = Activity(
        net_id=net_id,
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
    assert fetched.net_id == net_id


def test_default_activity(db: Session):
    net_id = _net_id(db, "net1")
    activity = Activity(
        net_id=net_id,
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
    net_id = _net_id(db, "net1")
    tag = ActivityTag(net_id=net_id, name="HF")
    db.add(tag)
    db.commit()

    fetched = db.get(ActivityTag, tag.id)
    assert fetched is not None
    assert fetched.name == "HF"
    assert fetched.net_id == net_id


def test_tag_name_unique_per_net(db: Session):
    """Two tags with the same name in the same net must fail."""
    net_id = _net_id(db, "net1")
    tag1 = ActivityTag(net_id=net_id, name="HF")
    db.add(tag1)
    db.commit()
    tag2 = ActivityTag(net_id=net_id, name="HF")
    db.add(tag2)
    with pytest.raises(Exception):
        db.commit()


def test_tag_name_not_unique_across_nets(db: Session):
    """Two nets may each have a tag with the same name — no DB error."""
    net1_id = _net_id(db, "net1")
    net2_id = _net_id(db, "net2")
    tag1 = ActivityTag(net_id=net1_id, name="checkin")
    tag2 = ActivityTag(net_id=net2_id, name="checkin")
    db.add_all([tag1, tag2])
    db.commit()  # must not raise

    assert db.get(ActivityTag, tag1.id) is not None
    assert db.get(ActivityTag, tag2.id) is not None


def test_activity_tag_assignment(db: Session):
    net_id = _net_id(db, "net1")
    activity = Activity(
        net_id=net_id,
        title="Test Activity",
        description="Test",
        instructions="Test instructions",
    )
    tag = ActivityTag(net_id=net_id, name="beginner-friendly")
    db.add_all([activity, tag])
    db.commit()

    assignment = ActivityTagAssignment(activity_id=activity.id, tag_id=tag.id)
    db.add(assignment)
    db.commit()

    db.refresh(activity)
    assert len(activity.tags) == 1
    assert activity.tags[0].name == "beginner-friendly"


def test_activity_usage(db: Session):
    net_id = _net_id(db, "net1")
    activity = Activity(
        net_id=net_id,
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


def test_activity_net_isolation(db: Session):
    """Activities in different nets are independent DB rows."""
    net1_id = _net_id(db, "net1")
    net2_id = _net_id(db, "net2")

    a1 = Activity(net_id=net1_id, title="A", description="d", instructions="i")
    a2 = Activity(net_id=net2_id, title="A", description="d", instructions="i")
    db.add_all([a1, a2])
    db.commit()

    assert a1.net_id == net1_id
    assert a2.net_id == net2_id
    assert a1.id != a2.id
