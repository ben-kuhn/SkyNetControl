from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth import secret_box
from backend.db.base import Base
from backend.modules.nets.models import Net
from backend.modules.nets.config_service import set_net_config
from backend.modules.events.models import Event, EventType, EventStatus
from backend.modules.events.event_config_service import set_event_config
from backend.integrations.winlink.pat_config import (
    resolve_pat_config, pat_transport_enabled,
    resolve_pat_config_for_event, pat_transport_enabled_for_event,
)

secret_box.install_key_material("test-secret")


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    net = Net(slug="t", name="T")
    db.add(net); db.flush()
    return db, net.id


def test_defaults_disabled():
    db, net_id = _db()
    cfg = resolve_pat_config(db, net_id)
    assert cfg.enabled is False
    assert cfg.base_url == ""
    assert pat_transport_enabled(db, net_id) is False


def test_basic_auth_decrypts_password():
    db, net_id = _db()
    set_net_config(db, net_id, "pat_transport_enabled", "true")
    set_net_config(db, net_id, "pat_http_base_url", "http://shack:8080")
    set_net_config(db, net_id, "pat_http_auth_mode", "basic")
    set_net_config(db, net_id, "pat_http_username", "op")
    set_net_config(db, net_id, "pat_http_password", secret_box.encrypt("s3cret"))
    cfg = resolve_pat_config(db, net_id)
    assert cfg.enabled is True
    assert cfg.base_url == "http://shack:8080"
    assert cfg.auth.mode == "basic"
    assert cfg.auth.username == "op"
    assert cfg.auth.password == "s3cret"


def test_timeout_default_and_override():
    db, net_id = _db()
    assert resolve_pat_config(db, net_id).timeout == 15.0
    set_net_config(db, net_id, "pat_http_timeout_seconds", "30")
    assert resolve_pat_config(db, net_id).timeout == 30.0


def _db_event():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    ev = Event(name="E", event_type=EventType.EMERGENCY, status=EventStatus.ACTIVE,
               created_by="W0NC", public_token="t", visibility="private")
    db.add(ev); db.flush()
    return db, ev.id


def test_event_defaults_disabled():
    db, event_id = _db_event()
    cfg = resolve_pat_config_for_event(db, event_id)
    assert cfg.enabled is False
    assert cfg.base_url == ""
    assert pat_transport_enabled_for_event(db, event_id) is False


def test_event_basic_auth_decrypts_password_once():
    db, event_id = _db_event()
    set_event_config(db, event_id, "pat_transport_enabled", "true")
    set_event_config(db, event_id, "pat_http_base_url", "http://evshack:8080")
    set_event_config(db, event_id, "pat_http_auth_mode", "basic")
    set_event_config(db, event_id, "pat_http_username", "evop")
    # set_event_config encrypts sensitive keys; get_event_config decrypts them.
    # resolve_pat_config_for_event must NOT decrypt again.
    set_event_config(db, event_id, "pat_http_password", "ev-s3cret")
    cfg = resolve_pat_config_for_event(db, event_id)
    assert cfg.enabled is True
    assert cfg.base_url == "http://evshack:8080"
    assert cfg.auth.mode == "basic"
    assert cfg.auth.username == "evop"
    assert cfg.auth.password == "ev-s3cret"
    assert pat_transport_enabled_for_event(db, event_id) is True


def test_event_timeout_default_and_override():
    db, event_id = _db_event()
    assert resolve_pat_config_for_event(db, event_id).timeout == 15.0
    set_event_config(db, event_id, "pat_http_timeout_seconds", "30")
    assert resolve_pat_config_for_event(db, event_id).timeout == 30.0
