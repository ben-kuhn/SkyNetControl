import json
import os

from sqlalchemy.orm import Session

from backend.auth.secret_box import decrypt
from backend.config_mgmt.models import AppConfig


# Keys whose values are stored encrypted at rest. The bulk PUT route in
# routes.py encrypts on write; reads here decrypt symmetrically so callers
# (delivery backends, callbook lookups, Claude API) always see plaintext.
# Recovery rotation (cli/recovery.py) keys off the same fragments.
SENSITIVE_KEY_FRAGMENTS = ("api_key", "password", "secret", "token")


def is_sensitive_key(key: str) -> bool:
    lk = key.lower()
    return any(fragment in lk for fragment in SENSITIVE_KEY_FRAGMENTS)


def _env_key_for(key: str) -> str:
    # Mirror Pydantic-settings: env_prefix=SKYNET_, env_nested_delimiter=__.
    # `pat_mailbox_path` → SKYNET_PAT_MAILBOX_PATH
    # `callbook.qrz.username` → SKYNET_CALLBOOK__QRZ__USERNAME
    return "SKYNET_" + key.upper().replace(".", "__")


DEFAULT_CHECKIN_MODES = [
    "Voice",
    "Winlink",
    "VARA",
    "VARA FM",
    "ARDOP",
    "Packet",
    "PACTOR",
    "Telnet",
    "AX.25",
    "CW",
    "Digital",
]


def get_config_value(db: Session, key: str, default: str | None = None) -> str | None:
    config = db.get(AppConfig, key)
    if config is not None:
        value = config.value
    else:
        env_value = os.environ.get(_env_key_for(key))
        value = env_value if env_value is not None else default
    if value and is_sensitive_key(key):
        # decrypt() is a no-op for plaintext (legacy rows, env-var fallback),
        # so it's safe to call unconditionally on sensitive keys.
        return decrypt(value)
    return value


def set_config_value(db: Session, key: str, value: str) -> None:
    config = db.get(AppConfig, key)
    if config is None:
        config = AppConfig(key=key, value=value)
        db.add(config)
    else:
        config.value = value
    db.commit()


def set_config_values_bulk(db: Session, values: dict[str, str]) -> None:
    """Upsert many config keys in a single transaction.

    Caller is responsible for any per-value pre-processing (e.g. encrypting
    sensitive values via secret_box). See backend.config_mgmt.routes for
    the route-level encryption policy.
    """
    for key, value in values.items():
        config = db.get(AppConfig, key)
        if config is None:
            db.add(AppConfig(key=key, value=value))
        else:
            config.value = value
    db.commit()


def get_all_config(db: Session) -> dict[str, str]:
    configs = db.query(AppConfig).all()
    return {c.key: c.value for c in configs}


def get_checkin_modes(db: Session) -> list[str]:
    raw = get_config_value(db, "checkins.modes")
    if raw is None:
        return DEFAULT_CHECKIN_MODES
    try:
        modes = json.loads(raw)
        if isinstance(modes, list) and all(isinstance(m, str) for m in modes):
            return modes
    except (json.JSONDecodeError, TypeError):
        pass
    return DEFAULT_CHECKIN_MODES
