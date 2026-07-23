# tests/test_aprs_event_config.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import Event, EventType, EventStatus
from backend.modules.events.event_config_service import set_event_config
from backend.integrations.aprs.manager import aprs_config


def _db_event():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    ev = Event(name="E", event_type=EventType.EMERGENCY, status=EventStatus.ACTIVE,
               created_by="W0NC", public_token="t", visibility="private")
    db.add(ev); db.flush()
    return db, ev


def test_aprs_config_off_by_default():
    db, ev = _db_event()
    assert aprs_config(db, ev.id) is None


def test_aprs_config_from_event_with_creator_callsign_default():
    db, ev = _db_event()
    set_event_config(db, ev.id, "aprs.enabled", "true")
    cfg = aprs_config(db, ev.id)
    assert cfg["callsign"] == "W0NC"          # defaulted to creator
    assert cfg["server"] == "rotate.aprs2.net"
    assert cfg["port"] == 14580
    set_event_config(db, ev.id, "aprs.callsign", "W0NC-9")
    assert aprs_config(db, ev.id)["callsign"] == "W0NC-9"   # override wins
