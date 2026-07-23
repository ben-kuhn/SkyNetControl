from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import Event, EventType, EventStatus
from backend.modules.events.event_config_service import set_event_config
from backend.integrations.delivery.service import _build_config, dispatch_delivery


def _db_event(created_by="W0NC"):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    ev = Event(name="E", event_type=EventType.EMERGENCY, status=EventStatus.ACTIVE,
               created_by=created_by, public_token="t", visibility="private")
    db.add(ev); db.flush()
    return db, ev


def test_event_delivery_uses_event_config(monkeypatch):
    db, ev = _db_event()
    set_event_config(db, ev.id, "pat_mailbox_path", "/tmp/evbox")
    captured = {}

    def fake_send(self, subject, body, config):
        captured.update(config)
        from backend.integrations.delivery.backends.base import DeliveryResult
        return DeliveryResult(success=True, error=None)

    from backend.integrations.delivery.backends.winlink import WinlinkBackend
    monkeypatch.setattr(WinlinkBackend, "send", fake_send)
    ok = dispatch_delivery(db, "event_message", 1, "s", "b", event_id=ev.id,
                           backends=["winlink"], config_overrides={"target_address": "KE0X"})
    assert ok is True
    assert captured["mailbox_path"] == "/tmp/evbox"    # sourced from EVENT config
    assert captured["target_address"] == "KE0X"


def test_callsign_falls_back_to_event_creator_when_net_address_empty():
    """Fix 6: when net_address is not configured for the event, callsign falls back
    to the event creator's callsign (not empty string)."""
    db, ev = _db_event(created_by="W0NC")
    # Deliberately do NOT set net_address — it stays unconfigured.
    config = _build_config(db, "winlink", event_id=ev.id)
    assert config["callsign"] == "W0NC", (
        f"Expected callsign='W0NC' (event creator fallback), got {config['callsign']!r}"
    )


def test_callsign_uses_net_address_when_set():
    """Fix 6: when net_address IS configured, callsign comes from it, not the creator."""
    db, ev = _db_event(created_by="W0NC")
    set_event_config(db, ev.id, "net_address", "W0NE@winlink.org")
    config = _build_config(db, "winlink", event_id=ev.id)
    assert config["callsign"] == "W0NE", (
        f"Expected callsign='W0NE' from net_address, got {config['callsign']!r}"
    )
