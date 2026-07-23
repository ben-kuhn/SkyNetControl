from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import Event, EventOperator, EventConfig, EventType, EventStatus


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_event_has_no_net_id_and_has_ownership_columns():
    cols = {c.name for c in Event.__table__.columns}
    assert "net_id" not in cols
    assert {"created_by", "public_token", "visibility"} <= cols


def test_event_row_roundtrip_without_net():
    db = _db()
    ev = Event(name="Skywarn", event_type=EventType.EMERGENCY, status=EventStatus.DRAFT,
               created_by="W0NC", public_token="tok123", visibility="private")
    db.add(ev); db.commit()
    got = db.query(Event).one()
    assert got.created_by == "W0NC"
    assert got.visibility == "private"


def test_event_operator_and_config_tables():
    db = _db()
    ev = Event(name="E", event_type=EventType.EMERGENCY, status=EventStatus.DRAFT,
               created_by="W0NC", public_token="t", visibility="private")
    db.add(ev); db.flush()
    db.add(EventOperator(event_id=ev.id, callsign="KD0OP", added_by="W0NC",
                         added_at=datetime.now(tz=timezone.utc)))
    db.add(EventConfig(event_id=ev.id, key="aprs.callsign", value="W0NC-9"))
    db.commit()
    assert db.query(EventOperator).one().callsign == "KD0OP"
    assert db.query(EventConfig).one().value == "W0NC-9"


def test_pat_session_net_id_nullable():
    from backend.integrations.winlink.models import PatConnectionSession
    assert PatConnectionSession.__table__.c.net_id.nullable is True
