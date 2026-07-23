# backend/modules/events/event_config_service.py
"""Per-event config: event override -> global AppConfig -> default. Sensitive keys
are encrypted at rest and decrypted on read, mirroring net/global config."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.auth import secret_box
from backend.config_mgmt.service import get_config_value, is_sensitive_key
from backend.modules.events.models import Event, EventConfig


def get_event_config(db: Session, event_id: int, key: str, default: str | None = None) -> str | None:
    row = db.get(EventConfig, (event_id, key))
    if row is not None:
        return secret_box.decrypt(row.value) if is_sensitive_key(key) else row.value
    # fall back to global AppConfig (which itself decrypts sensitive globals), then default
    return get_config_value(db, key, default)


def set_event_config(db: Session, event_id: int, key: str, value: str) -> None:
    stored = secret_box.encrypt(value) if (is_sensitive_key(key) and value) else value
    row = db.get(EventConfig, (event_id, key))
    if row is None:
        db.add(EventConfig(event_id=event_id, key=key, value=stored))
    else:
        row.value = stored
        row.updated_at = datetime.now(timezone.utc)
    db.commit()


def set_event_config_bulk(db: Session, event_id: int, values: dict[str, str]) -> None:
    now = datetime.now(timezone.utc)
    for key, value in values.items():
        stored = secret_box.encrypt(value) if (is_sensitive_key(key) and value) else value
        row = db.get(EventConfig, (event_id, key))
        if row is None:
            db.add(EventConfig(event_id=event_id, key=key, value=stored))
        else:
            row.value = stored
            row.updated_at = now
    db.commit()


def event_from_callsign(db: Session, event: Event) -> str:
    """The event's Winlink/APRS 'from' callsign: net_address override -> global ->
    the event creator's callsign."""
    net_address = get_event_config(db, event.id, "net_address", "") or ""
    if net_address:
        return net_address.split("@")[0].upper()
    return (event.created_by or "").upper()
