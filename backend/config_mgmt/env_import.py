"""One-shot env-to-AppConfig importer.

Scans an env-var mapping for the legacy SkyNetControl patterns
(`SKYNET_AUTH_*`, `SKYNET_SMTP__*`, `SKYNET_AUTH_OIDC_*_*`) and writes
the matching `oauth.<slug>.*` / `smtp.*` rows into `app_config`. Called
by the Alembic data migration that lands alongside Phase 2a, and unit
tested directly.

Idempotent: existing `oauth.*` / `smtp.*` rows are never overwritten —
if any are already present for a given slug or for SMTP, the env values
for that section are ignored. This protects against the migration ever
clobbering admin edits made via the Config page.

If any oauth/smtp rows were written OR any pre-existing AppConfig rows
were present before the call, `setup_completed` is marked true so the
existing deployment skips the wizard on next boot.
"""

from __future__ import annotations

import re
from typing import Mapping

from sqlalchemy.orm import Session

from backend.auth.oidc_slug import slug_from_env_middle, validate_slug
from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.oauth import (
    OAuthProviderConfig,
    get_oauth_provider,
    upsert_oauth_provider,
)
from backend.config_mgmt.setup_state import mark_setup_completed
from backend.config_mgmt.smtp import SmtpConfig, get_smtp_config, upsert_smtp_config

_FIXED_PROVIDERS = ("google", "microsoft", "github", "discord", "facebook")

_OIDC_ENV_RE = re.compile(
    r"^SKYNET_AUTH_OIDC_([A-Z0-9_]+)_(NAME|ENABLED|CLIENT_ID|CLIENT_SECRET|ISSUER_URL)$"
)
_FIXED_ENV_RE = re.compile(
    r"^SKYNET_AUTH_(GOOGLE|MICROSOFT|GITHUB|DISCORD|FACEBOOK)__(ENABLED|CLIENT_ID|CLIENT_SECRET)$"
)
_SMTP_KEYS = {
    "SKYNET_SMTP__HOST": "host",
    "SKYNET_SMTP__PORT": "port",
    "SKYNET_SMTP__USERNAME": "username",
    "SKYNET_SMTP__PASSWORD": "password",
    "SKYNET_SMTP__FROM_ADDRESS": "from_address",
    "SKYNET_SMTP__USE_TLS": "use_tls",
}


def import_env_to_app_config(db: Session, env: Mapping[str, str]) -> None:
    """Scan `env` for the legacy SKYNET_* patterns and persist them.

    Existing rows are never overwritten. After the scan, `setup_completed`
    is marked true if anything is present in `app_config` (whether just
    written, or pre-existing from prior wizard runs).
    """
    pre_existing_rows = db.query(AppConfig).count()

    _import_fixed_providers(db, env)
    _import_oidc_providers(db, env)
    _import_smtp(db, env)

    if pre_existing_rows > 0 or db.query(AppConfig).count() > 0:
        mark_setup_completed(db)


def _import_fixed_providers(db: Session, env: Mapping[str, str]) -> None:
    by_slug: dict[str, dict[str, str]] = {}
    for key, value in env.items():
        m = _FIXED_ENV_RE.match(key)
        if not m:
            continue
        slug = m.group(1).lower()
        field = m.group(2).lower()
        by_slug.setdefault(slug, {})[field] = value

    for slug, fields in by_slug.items():
        if get_oauth_provider(db, slug) is not None:
            continue  # never overwrite admin/wizard data
        enabled = fields.get("enabled", "false").lower() == "true"
        client_id = fields.get("client_id", "")
        client_secret = fields.get("client_secret", "")
        if not enabled and not client_id and not client_secret:
            continue  # blank, skip
        upsert_oauth_provider(db, OAuthProviderConfig(
            slug=slug,
            name=slug.title(),
            enabled=enabled,
            client_id=client_id,
            client_secret=client_secret,
            issuer_url="",
        ))


def _import_oidc_providers(db: Session, env: Mapping[str, str]) -> None:
    by_middle: dict[str, dict[str, str]] = {}
    for key, value in env.items():
        m = _OIDC_ENV_RE.match(key)
        if not m:
            continue
        middle, field = m.group(1), m.group(2).lower()
        by_middle.setdefault(middle, {})[field] = value

    for middle, fields in by_middle.items():
        slug = slug_from_env_middle(middle)
        if validate_slug(slug) is not None:
            continue  # invalid (e.g. starts with a digit, or reserved)
        if get_oauth_provider(db, slug) is not None:
            continue  # already configured by wizard / Config page
        enabled = fields.get("enabled", "false").lower() == "true"
        client_id = fields.get("client_id", "")
        client_secret = fields.get("client_secret", "")
        issuer_url = fields.get("issuer_url", "")
        if not enabled and not client_id and not client_secret and not issuer_url:
            continue  # blank, skip — symmetric with fixed-provider handling
        upsert_oauth_provider(db, OAuthProviderConfig(
            slug=slug,
            name=fields.get("name", slug.title()),
            enabled=enabled,
            client_id=client_id,
            client_secret=client_secret,
            issuer_url=issuer_url,
        ))


def _import_smtp(db: Session, env: Mapping[str, str]) -> None:
    if get_smtp_config(db) is not None:
        return  # already configured

    fields: dict[str, str] = {}
    for env_key, smtp_key in _SMTP_KEYS.items():
        if env_key in env:
            fields[smtp_key] = env[env_key]

    if "host" not in fields:
        return  # SMTP not set in env

    try:
        port = int(fields.get("port", "587"))
    except ValueError:
        return  # corrupt env, skip rather than crash the migration

    upsert_smtp_config(db, SmtpConfig(
        host=fields["host"],
        port=port,
        username=fields.get("username", ""),
        password=fields.get("password", ""),
        from_address=fields.get("from_address", ""),
        use_tls=fields.get("use_tls", "false").lower() == "true",
    ))
