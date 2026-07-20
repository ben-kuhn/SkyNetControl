"""Resolve PAT HTTP transport settings (per-net with global fallback), decrypt
secrets, and construct a configured PatClient."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.auth import secret_box
from backend.config_mgmt.service import get_config_value
from backend.modules.nets.config_service import get_net_config
from backend.integrations.winlink.pat_client import PatAuth, PatClient

PAT_HTTP_KEYS = (
    "pat_transport_enabled",
    "pat_http_base_url",
    "pat_http_auth_mode",
    "pat_http_username",
    "pat_http_password",
    "pat_http_token",
    "pat_http_timeout_seconds",
)


@dataclass
class PatHttpConfig:
    base_url: str
    auth: PatAuth
    timeout: float
    enabled: bool


def _get(db: Session, net_id: int, key: str, default: str = "") -> str:
    val = get_net_config(db, net_id, key)
    if val is None:
        val = get_config_value(db, key)
    return val if val is not None else default


def resolve_pat_config(db: Session, net_id: int) -> PatHttpConfig:
    enabled = _get(db, net_id, "pat_transport_enabled").strip().lower() == "true"
    base_url = _get(db, net_id, "pat_http_base_url").strip()
    mode = _get(db, net_id, "pat_http_auth_mode", "none").strip().lower() or "none"
    username = _get(db, net_id, "pat_http_username")
    password = secret_box.decrypt(_get(db, net_id, "pat_http_password"))
    token = secret_box.decrypt(_get(db, net_id, "pat_http_token"))
    raw_timeout = _get(db, net_id, "pat_http_timeout_seconds", "15").strip()
    try:
        timeout = float(raw_timeout)
    except ValueError:
        timeout = 15.0
    return PatHttpConfig(
        base_url=base_url,
        auth=PatAuth(mode=mode, username=username, password=password, token=token),
        timeout=timeout,
        enabled=enabled,
    )


def pat_transport_enabled(db: Session, net_id: int) -> bool:
    cfg = resolve_pat_config(db, net_id)
    return cfg.enabled and bool(cfg.base_url)


def build_pat_client(cfg: PatHttpConfig) -> PatClient:
    return PatClient(cfg.base_url, auth=cfg.auth, timeout=cfg.timeout)
