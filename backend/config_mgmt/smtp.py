from dataclasses import dataclass

from sqlalchemy.orm import Session

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
    """Return the configured SMTP settings, or None if `smtp.host` is unset."""
    host = _row(db, "smtp.host")
    if not host:
        return None
    return SmtpConfig(
        host=host,
        port=int(_row(db, "smtp.port") or "0"),
        username=_row(db, "smtp.username") or "",
        password=_row(db, "smtp.password") or "",
        from_address=_row(db, "smtp.from_address") or "",
        use_tls=(_row(db, "smtp.use_tls") or "false").lower() == "true",
    )


def upsert_smtp_config(db: Session, cfg: SmtpConfig) -> None:
    """Write all SMTP fields to app_config, overwriting any existing values."""
    values = {
        "smtp.host": cfg.host,
        "smtp.port": str(cfg.port),
        "smtp.username": cfg.username,
        "smtp.password": cfg.password,
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
