import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.integrations.aprs import manager
from backend.integrations.aprs.beacon import desired_objects, kill_all, send_objects
from backend.modules.events.models import EventType
from backend.modules.events.event_config_service import set_event_config_bulk
from backend.modules.events.service import activate_event, close_event, create_event, create_post, update_event

CONFIG = {"callsign": "W0NE", "server": "x", "port": 1}


class CollectingWriter:
    """Duck-typed StreamWriter capturing written lines."""

    def __init__(self):
        self.lines: list[str] = []

    def write(self, data: bytes):
        self.lines.append(data.decode().strip())

    async def drain(self):
        pass

    def close(self):
        pass


@pytest.fixture
def db_factory():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def event_with_posts(db_factory):
    with db_factory() as db:
        event = create_event(
            db, name="Marathon", event_type=EventType.PUBLIC_SERVICE,
            created_by="W0NE",
        )
        activate_event(db, event.id, actor="W0NE")
        set_event_config_bulk(db, event.id, {"aprs.enabled": "true", "aprs.callsign": "W0NE"})
        create_post(db, event.id, name="Rest Stop 3", lat=39.0625, lon=-94.5786)
        create_post(db, event.id, name="No Coords Post")  # must not beacon
        update_event(db, event.id, aprs_beacon_posts=True)
        return event.id


def make_state(db_factory, event_id):
    return manager.AprsClientState(event_id=event_id, session_factory=db_factory)


class TestDesired:
    def test_only_posts_with_coords(self, db_factory, event_with_posts):
        state = make_state(db_factory, event_with_posts)
        desired = desired_objects(state)
        assert list(desired.keys()) == ["RESTSTOP3"]
        name, (lat, lon, post_id) = next(iter(desired.items()))
        assert (lat, lon) == (39.0625, -94.5786)

    def test_empty_when_toggle_off(self, db_factory, event_with_posts):
        with db_factory() as db:
            update_event(db, event_with_posts, aprs_beacon_posts=False)
        state = make_state(db_factory, event_with_posts)
        assert desired_objects(state) == {}

    def test_empty_when_event_closed(self, db_factory, event_with_posts):
        with db_factory() as db:
            close_event(db, event_with_posts, actor="W0NE")
        state = make_state(db_factory, event_with_posts)
        assert desired_objects(state) == {}


class TestSendObjects:
    async def test_initial_beacon_and_bookkeeping(self, db_factory, event_with_posts):
        state = make_state(db_factory, event_with_posts)
        writer = CollectingWriter()
        await send_objects(state, writer, CONFIG, force=True)
        assert len(writer.lines) == 1
        assert writer.lines[0].startswith("W0NE>APZSNC,TCPIP*:;RESTSTOP3*")
        assert "SkyNetControl event: Marathon" in writer.lines[0]
        assert state.announced == {"RESTSTOP3": (39.0625, -94.5786)}
        assert list(state.objects_by_post.values()) == ["RESTSTOP3"]

    async def test_no_resend_without_force_or_change(self, db_factory, event_with_posts):
        state = make_state(db_factory, event_with_posts)
        writer = CollectingWriter()
        await send_objects(state, writer, CONFIG, force=True)
        writer.lines.clear()
        await send_objects(state, writer, CONFIG)  # nothing changed
        assert writer.lines == []

    async def test_removed_post_gets_kill(self, db_factory, event_with_posts):
        state = make_state(db_factory, event_with_posts)
        writer = CollectingWriter()
        await send_objects(state, writer, CONFIG, force=True)
        with db_factory() as db:
            update_event(db, event_with_posts, aprs_beacon_posts=False)
        writer.lines.clear()
        await send_objects(state, writer, CONFIG)
        assert len(writer.lines) == 1
        assert ";RESTSTOP3_" in writer.lines[0]
        assert state.announced == {}

    async def test_kill_all(self, db_factory, event_with_posts):
        state = make_state(db_factory, event_with_posts)
        writer = CollectingWriter()
        await send_objects(state, writer, CONFIG, force=True)
        writer.lines.clear()
        await kill_all(state, writer, CONFIG)
        assert any(";RESTSTOP3_" in line for line in writer.lines)
        assert state.announced == {}
