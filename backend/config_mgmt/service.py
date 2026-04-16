from sqlalchemy.orm import Session

from backend.config_mgmt.models import AppConfig


def get_config_value(db: Session, key: str, default: str | None = None) -> str | None:
    config = db.get(AppConfig, key)
    if config is None:
        return default
    return config.value


def set_config_value(db: Session, key: str, value: str) -> None:
    config = db.get(AppConfig, key)
    if config is None:
        config = AppConfig(key=key, value=value)
        db.add(config)
    else:
        config.value = value
    db.commit()


def get_all_config(db: Session) -> dict[str, str]:
    configs = db.query(AppConfig).all()
    return {c.key: c.value for c in configs}
