"""Per-event config: masked read, encrypting bulk write. CONTROL-gated.

NOTE: unlike the per-net config route (which encrypts at the route because
set_net_config does not), the EVENT config service ALREADY encrypts sensitive
values on write (event_config_service.set_event_config_bulk, line 37) and
decrypts on read (get_event_config, line 18). So this route passes PLAINTEXT to
the service and must NOT pre-encrypt — doing so would double-encrypt and corrupt
the round-trip. The GET still masks sensitive rows as "***" so ciphertext never
leaves the server."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session
from backend.config_mgmt.service import is_sensitive_key
from backend.modules.events.event_auth import EventContext, EventRole, require_event_role
from backend.modules.events.event_config_service import set_event_config_bulk
from backend.modules.events.models import EventConfig

event_config_router = APIRouter(prefix="/api/events", tags=["events", "config"])


class ConfigBulkBody(BaseModel):
    values: dict[str, str]


@event_config_router.get("/{event_id}/config")
async def get_event_config_route(
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
) -> dict[str, str]:
    rows = db.query(EventConfig).filter(EventConfig.event_id == ctx.event.id).all()
    # Mask sensitive values as "***" — never return secrets (even the ciphertext).
    return {r.key: ("***" if is_sensitive_key(r.key) and r.value else r.value) for r in rows}


@event_config_router.put("/{event_id}/config/bulk")
async def put_event_config_bulk_route(
    body: ConfigBulkBody,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
) -> dict[str, bool]:
    # Pass plaintext straight through — the service encrypts sensitive keys itself.
    set_event_config_bulk(db, ctx.event.id, body.values)
    return {"ok": True}
