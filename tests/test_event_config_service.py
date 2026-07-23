# tests/test_event_config_service.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth import secret_box
from backend.db.base import Base
from backend.config_mgmt.service import set_config_value
from backend.modules.events.models import Event, EventType, EventStatus
from backend.modules.events.event_config_service import (
    get_event_config, set_event_config, event_from_callsign,
)

secret_box.install_key_material("test-secret")


def _db_event(callsign="W0NC"):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    ev = Event(name="E", event_type=EventType.EMERGENCY, status=EventStatus.ACTIVE,
               created_by=callsign, public_token="t", visibility="private")
    db.add(ev); db.flush()
    return db, ev


def test_override_beats_global_beats_default():
    db, ev = _db_event()
    assert get_event_config(db, ev.id, "aprs.server", "d") == "d"           # default
    set_config_value(db, "aprs.server", "global.aprs2.net")
    assert get_event_config(db, ev.id, "aprs.server", "d") == "global.aprs2.net"  # global
    set_event_config(db, ev.id, "aprs.server", "event.aprs2.net")
    assert get_event_config(db, ev.id, "aprs.server", "d") == "event.aprs2.net"   # override


def test_sensitive_key_encrypted_at_rest_and_decrypted_on_read():
    db, ev = _db_event()
    set_event_config(db, ev.id, "pat_http_password", "s3cret")
    from backend.modules.events.models import EventConfig
    raw = db.get(EventConfig, (ev.id, "pat_http_password")).value
    assert raw != "s3cret" and raw.startswith("enc:")
    assert get_event_config(db, ev.id, "pat_http_password") == "s3cret"


def test_from_callsign_defaults_to_creator():
    db, ev = _db_event(callsign="KE0ABC")
    assert event_from_callsign(db, ev) == "KE0ABC"          # default: creator
    set_event_config(db, ev.id, "net_address", "W0NE@winlink.org")
    assert event_from_callsign(db, ev) == "W0NE"            # override wins
