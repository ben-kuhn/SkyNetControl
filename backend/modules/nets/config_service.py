"""Per-net configuration get/set service.

Keys and values are arbitrary strings stored in the net_config table.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.modules.nets.models import NetConfig


def get_net_config(db: Session, net_id: int, key: str, default: str | None = None) -> str | None:
    row = db.get(NetConfig, (net_id, key))
    return row.value if row else default


def set_net_config(db: Session, net_id: int, key: str, value: str) -> None:
    row = db.get(NetConfig, (net_id, key))
    if row is None:
        db.add(NetConfig(net_id=net_id, key=key, value=value))
    else:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
    db.commit()
