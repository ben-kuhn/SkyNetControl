from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.audit.service import log_action
from backend.auth.dependencies import Principal, get_db_session, require_admin_or_recovery
from backend.auth.secret_box import encrypt
from backend.config_mgmt.service import (
    get_all_config,
    is_sensitive_key,
    set_config_value,
    set_config_values_bulk,
)

config_router = APIRouter(tags=["config"])


class ConfigValueRequest(BaseModel):
    value: str


class ConfigBulkRequest(BaseModel):
    values: dict[str, str]


@config_router.get("/")
async def list_config(
    principal: Principal = Depends(require_admin_or_recovery),
    db: Session = Depends(get_db_session),
):
    # Mirror the per-domain GET handlers (oauth_routes, smtp_routes) which
    # mask secret values as "***". The bulk endpoint previously returned
    # OAuth client_secret and SMTP password verbatim — defeating the masking
    # discipline that the typed routes follow. Frontend never needs the
    # actual secret; "***" is enough to indicate "set, but not shown."
    raw = get_all_config(db)
    return {k: ("***" if is_sensitive_key(k) and v else v) for k, v in raw.items()}


@config_router.put("/bulk")
async def update_config_bulk(
    body: ConfigBulkRequest,
    principal: Principal = Depends(require_admin_or_recovery),
    db: Session = Depends(get_db_session),
):
    # Encrypt sensitive values on the way down, mirroring the single-key
    # route's behavior (and what the typed OAuth/SMTP routes do via
    # secret_box). Empty strings pass through unencrypted.
    prepared: dict[str, str] = {}
    for key, value in body.values.items():
        if is_sensitive_key(key) and value:
            prepared[key] = encrypt(value)
        else:
            prepared[key] = value
    set_config_values_bulk(db, prepared)
    for key, value in body.values.items():
        audit_value = "[REDACTED]" if is_sensitive_key(key) else value
        log_action(
            db,
            actor=principal.callsign,
            action="config.updated",
            details={"key": key, "value": audit_value},
        )
    return {"ok": True, "count": len(body.values)}


@config_router.put("/{key}")
async def update_config(
    key: str,
    body: ConfigValueRequest,
    principal: Principal = Depends(require_admin_or_recovery),
    db: Session = Depends(get_db_session),
):
    # If the operator sets a sensitive key directly through the bulk endpoint
    # (escape hatch for keys the typed routes don't cover), encrypt it on
    # the way down so it isn't written plaintext. The typed routes
    # (oauth_routes, smtp_routes) already encrypt via secret_box; this
    # mirrors that for everything else marked sensitive.
    stored_value = encrypt(body.value) if is_sensitive_key(key) and body.value else body.value
    set_config_value(db, key, stored_value)
    audit_value = "[REDACTED]" if is_sensitive_key(key) else body.value
    log_action(db, actor=principal.callsign, action="config.updated", details={"key": key, "value": audit_value})
    return {"key": key, "value": body.value}
