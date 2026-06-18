from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.auth.secret_box import decrypt, encrypt
from backend.config_mgmt.models import AppConfig


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    from_address: str
    use_tls: bool


_KEYS = (
    "smtp.host",
    "smtp.port",
    "smtp.username",
    "smtp.password",
    "smtp.from_address",
    "smtp.use_tls",
)


def _row(db: Session, key: str) -> str | None:
    obj = db.get(AppConfig, key)
    return None if obj is None else obj.value


def get_smtp_config(db: Session) -> SmtpConfig | None:
    """Return the configured SMTP settings, or None if host or port are missing or invalid.

    A missing/blank/unparseable `smtp.port` is treated as "not configured"
    rather than silently coerced to 0 — port 0 would later produce an
    obscure SMTP connection failure that's harder to diagnose than a
    no-op email send.
    """
    host = _row(db, "smtp.host")
    if not host:
        return None
    port_raw = _row(db, "smtp.port")
    if not port_raw:
        return None
    try:
        port = int(port_raw)
    except ValueError:
        return None
    return SmtpConfig(
        host=host,
        port=port,
        username=_row(db, "smtp.username") or "",
        # password is the only sensitive field; decrypt on read. Plaintext
        # rows from before secret_box landed pass through unchanged and
        # re-encrypt next time the admin saves.
        password=decrypt(_row(db, "smtp.password") or ""),
        from_address=_row(db, "smtp.from_address") or "",
        use_tls=(_row(db, "smtp.use_tls") or "false").lower() == "true",
    )


def upsert_smtp_config(db: Session, cfg: SmtpConfig) -> None:
    """Write all SMTP fields to app_config, overwriting any existing values."""
    values = {
        "smtp.host": cfg.host,
        "smtp.port": str(cfg.port),
        "smtp.username": cfg.username,
        # encrypt("") -> "" so the no-auth SMTP case still round-trips.
        "smtp.password": encrypt(cfg.password),
        "smtp.from_address": cfg.from_address,
        "smtp.use_tls": "true" if cfg.use_tls else "false",
    }
    for key, value in values.items():
        obj = db.get(AppConfig, key)
        if obj is None:
            db.add(AppConfig(key=key, value=value))
        else:
            obj.value = value
    db.commit()


def clear_smtp_config(db: Session) -> None:
    """Remove every `smtp.*` row from app_config."""
    for key in _KEYS:
        obj = db.get(AppConfig, key)
        if obj is not None:
            db.delete(obj)
    db.commit()
