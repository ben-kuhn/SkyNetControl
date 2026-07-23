from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import Event, EventType, EventStatus
from backend.modules.events.event_config_service import set_event_config
from backend.integrations.delivery.service import dispatch_delivery


def _db_event():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    ev = Event(name="E", event_type=EventType.EMERGENCY, status=EventStatus.ACTIVE,
               created_by="W0NC", public_token="t", visibility="private")
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
