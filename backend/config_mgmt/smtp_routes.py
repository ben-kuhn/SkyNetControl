from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.audit.service import log_action
from backend.auth.dependencies import Principal, get_db_session, require_admin_or_recovery
from backend.config_mgmt.smtp import SmtpConfig, clear_smtp_config, get_smtp_config, upsert_smtp_config

smtp_router = APIRouter(prefix="/smtp", tags=["admin-smtp"])

_REDACTED = "***"


class SmtpResponse(BaseModel):
    host: str
    port: int
    username: str
    password: str  # always "***" or "" — never the real password
    from_address: str
    use_tls: bool


class SmtpUpsert(BaseModel):
    host: str
    port: int
    username: str
    # "" = preserve existing value; "-" = clear; anything else = set.
    # Unlike OAuth providers, a blank password on *create* is allowed —
    # anonymous SMTP relays are a legitimate use-case.
    password: str
    from_address: str
    use_tls: bool


def _to_response(cfg: SmtpConfig) -> SmtpResponse:
    return SmtpResponse(
        host=cfg.host,
        port=cfg.port,
        username=cfg.username,
        password=_REDACTED if cfg.password else "",
        from_address=cfg.from_address,
        use_tls=cfg.use_tls,
    )


@smtp_router.get("")
def get_smtp(
    _: Principal = Depends(require_admin_or_recovery),
    db: Session = Depends(get_db_session),
) -> SmtpResponse:
    cfg = get_smtp_config(db)
    if cfg is None:
        raise HTTPException(404)
    return _to_response(cfg)


@smtp_router.put("")
def upsert_smtp(
    body: SmtpUpsert,
    principal: Principal = Depends(require_admin_or_recovery),
    db: Session = Depends(get_db_session),
) -> SmtpResponse:
    existing = get_smtp_config(db)
    if body.password == "":
        # "" means "preserve existing"; on first create we have no existing
        # password, so preserve means use "" (anonymous relay). This is valid
        # for SMTP (unlike OAuth where a missing secret breaks the flow).
        password = existing.password if existing else ""
    elif body.password == "-":
        password = ""
    else:
        password = body.password
    cfg = SmtpConfig(
        host=body.host,
        port=body.port,
        username=body.username,
        password=password,
        from_address=body.from_address,
        use_tls=body.use_tls,
    )
    upsert_smtp_config(db, cfg)
    log_action(
        db,
        actor=principal.callsign,
        action="smtp.upserted",
        details={
            "host": body.host,
            "port": body.port,
            "username": body.username,
            "from_address": body.from_address,
            "use_tls": body.use_tls,
            "secret_changed": body.password not in ("", _REDACTED),
        },
    )
    return _to_response(cfg)


@smtp_router.delete("", status_code=204)
def delete_smtp(
    principal: Principal = Depends(require_admin_or_recovery),
    db: Session = Depends(get_db_session),
) -> None:
    clear_smtp_config(db)
    log_action(
        db,
        actor=principal.callsign,
        action="smtp.cleared",
        details={},
    )
