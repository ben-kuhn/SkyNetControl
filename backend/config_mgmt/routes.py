from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.audit.service import log_action
from backend.auth.dependencies import Principal, get_db_session, require_admin_or_recovery
from backend.config_mgmt.service import get_all_config, set_config_value

config_router = APIRouter(tags=["config"])


# Config keys whose values are treated as secrets: never written to the audit log.
# Any key matching one of these substrings (case-insensitive) is redacted.
SENSITIVE_KEY_FRAGMENTS = ("api_key", "password", "secret", "token")


def _is_sensitive_key(key: str) -> bool:
    lk = key.lower()
    return any(fragment in lk for fragment in SENSITIVE_KEY_FRAGMENTS)


class ConfigValueRequest(BaseModel):
    value: str


@config_router.get("/")
async def list_config(
    principal: Principal = Depends(require_admin_or_recovery),
    db: Session = Depends(get_db_session),
):
    return get_all_config(db)


@config_router.put("/{key}")
async def update_config(
    key: str,
    body: ConfigValueRequest,
    principal: Principal = Depends(require_admin_or_recovery),
    db: Session = Depends(get_db_session),
):
    set_config_value(db, key, body.value)
    audit_value = "[REDACTED]" if _is_sensitive_key(key) else body.value
    log_action(db, actor=principal.callsign, action="config.updated", details={"key": key, "value": audit_value})
    return {"key": key, "value": body.value}
