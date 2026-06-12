from sqlalchemy.orm import Session

from backend.config_mgmt.models import AppConfig

_SENTINEL_KEY = "setup_completed"


def is_setup_completed(db: Session) -> bool:
    """Return True iff the setup_completed sentinel row exists with value "true"."""
    row = db.get(AppConfig, _SENTINEL_KEY)
    return row is not None and row.value.lower() == "true"


def mark_setup_completed(db: Session) -> None:
    """Set the setup_completed sentinel. Idempotent."""
    row = db.get(AppConfig, _SENTINEL_KEY)
    if row is None:
        db.add(AppConfig(key=_SENTINEL_KEY, value="true"))
    else:
        row.value = "true"
    db.commit()
