import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import (
    Event,
    EventLogEntry,
    EventLogType,
    EventParticipant,
    EventStatus,
    EventType,
)
from backend.modules.events.service import (
    DuplicatePostError,
    EventNotActiveError,
    InvalidLifecycleError,
    PostAssignedError,
    activate_event,
    close_event,
    create_event,
    create_post,
    delete_post,
    reopen_event,
    update_event,
    update_post,
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
def net(db):
    return make_test_net(db)


def _log_messages(db, event_id):
    entries = (
        db.query(EventLogEntry)
        .filter(EventLogEntry.event_id == event_id)
        .order_by(EventLogEntry.seq)
        .all()
    )
    return [(e.seq, e.entry_type, e.message) for e in entries]


class TestLifecycle:
    def test_create_draft(self, db, net):
        event = create_event(
            db, net_id=net.id, name="Marathon", event_type=EventType.PUBLIC_SERVICE, created_by="W0NE"
        )
        assert event.status == EventStatus.DRAFT
        assert event.activated_at is None
        assert _log_messages(db, event.id) == []

    def test_create_and_activate(self, db, net):
        event = create_event(
            db, net_id=net.id, name="Tornado", event_type=EventType.EMERGENCY,
            created_by="W0NE", activate=True,
        )
        assert event.status == EventStatus.ACTIVE
        assert event.activated_at is not None
        msgs = _log_messages(db, event.id)
        assert msgs == [(1, EventLogType.SYSTEM, "Event activated")]
        assert event.log_seq == 1

    def test_activate_draft(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.PUBLIC_SERVICE, created_by="W0NE"
        )
        result = activate_event(db, event.id, actor="W0NC")
        assert result.status == EventStatus.ACTIVE
        assert result.activated_at is not None

    def test_activate_non_draft_raises(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE", activate=True
        )
        with pytest.raises(InvalidLifecycleError):
            activate_event(db, event.id, actor="W0NE")

    def test_close_active(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE", activate=True
        )
        result = close_event(db, event.id, actor="W0NE")
        assert result.status == EventStatus.CLOSED
        assert result.closed_at is not None
        assert _log_messages(db, event.id)[-1][2] == "Event closed"

    def test_close_draft_raises(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.PUBLIC_SERVICE, created_by="W0NE"
        )
        with pytest.raises(InvalidLifecycleError):
            close_event(db, event.id, actor="W0NE")

    def test_reopen_closed(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE", activate=True
        )
        close_event(db, event.id, actor="W0NE")
        result = reopen_event(db, event.id, actor="W0NE")
        assert result.status == EventStatus.ACTIVE
        assert result.closed_at is None
        assert _log_messages(db, event.id)[-1][2] == "Event reopened"

    def test_reopen_active_raises(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE", activate=True
        )
        with pytest.raises(InvalidLifecycleError):
            reopen_event(db, event.id, actor="W0NE")

    def test_seq_is_monotonic(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE", activate=True
        )
        close_event(db, event.id, actor="W0NE")
        reopen_event(db, event.id, actor="W0NE")
        seqs = [s for s, _, _ in _log_messages(db, event.id)]
        assert seqs == [1, 2, 3]
        db.refresh(event)
        assert event.log_seq == 3

    def test_update_event_fields(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.PUBLIC_SERVICE, created_by="W0NE"
        )
        result = update_event(db, event.id, name="Renamed", description="desc")
        assert result.name == "Renamed"
        assert result.description == "desc"

    def test_update_closed_event_raises(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE", activate=True
        )
        close_event(db, event.id, actor="W0NE")
        with pytest.raises(EventNotActiveError):
            update_event(db, event.id, name="Nope")


class TestPosts:
    @pytest.fixture
    def event(self, db, net):
        return create_event(
            db, net_id=net.id, name="Marathon", event_type=EventType.PUBLIC_SERVICE,
            created_by="W0NE", activate=True,
        )

    def test_create_post(self, db, event):
        post = create_post(db, event.id, name="Rest Stop 3", lat=39.1, lon=-94.6)
        assert post.id is not None
        assert post.lat == 39.1

    def test_create_post_on_draft_event(self, db, net):
        draft = create_event(
            db, net_id=net.id, name="D", event_type=EventType.PUBLIC_SERVICE, created_by="W0NE"
        )
        post = create_post(db, draft.id, name="SAG 1")
        assert post.id is not None

    def test_create_post_on_closed_event_raises(self, db, event):
        close_event(db, event.id, actor="W0NE")
        with pytest.raises(EventNotActiveError):
            create_post(db, event.id, name="Late")

    def test_duplicate_post_name_raises(self, db, event):
        create_post(db, event.id, name="EOC")
        with pytest.raises(DuplicatePostError):
            create_post(db, event.id, name="EOC")

    def test_update_post(self, db, event):
        post = create_post(db, event.id, name="EOC")
        result = update_post(db, event.id, post.id, name="EOC Main", lat=39.0)
        assert result.name == "EOC Main"
        assert result.lat == 39.0

    def test_update_post_duplicate_name_raises(self, db, event):
        create_post(db, event.id, name="EOC")
        post2 = create_post(db, event.id, name="Shelter A")
        with pytest.raises(DuplicatePostError):
            update_post(db, event.id, post2.id, name="EOC")

    def test_delete_unassigned_post(self, db, event):
        post = create_post(db, event.id, name="EOC")
        assert delete_post(db, event.id, post.id) is True

    def test_delete_assigned_post_raises(self, db, event):
        post = create_post(db, event.id, name="EOC")
        db.add(EventParticipant(event_id=event.id, callsign="KE0XYZ", post_id=post.id))
        db.commit()
        with pytest.raises(PostAssignedError):
            delete_post(db, event.id, post.id)

    def test_delete_missing_post_returns_false(self, db, event):
        assert delete_post(db, event.id, 9999) is False
