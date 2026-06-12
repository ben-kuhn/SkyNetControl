from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.config_mgmt.models import AppConfig


@dataclass(frozen=True)
class OAuthProviderConfig:
    slug: str
    name: str
    enabled: bool
    client_id: str
    client_secret: str
    issuer_url: str  # empty string for non-OIDC providers


_FIELDS = ("name", "enabled", "client_id", "client_secret", "issuer_url")


def _key(slug: str, field: str) -> str:
    return f"oauth.{slug}.{field}"


def _row(db: Session, key: str) -> str | None:
    obj = db.get(AppConfig, key)
    return None if obj is None else obj.value


def _build(slug: str, rows: dict[str, str]) -> OAuthProviderConfig:
    return OAuthProviderConfig(
        slug=slug,
        name=rows.get("name", ""),
        enabled=rows.get("enabled", "false").lower() == "true",
        client_id=rows.get("client_id", ""),
        client_secret=rows.get("client_secret", ""),
        issuer_url=rows.get("issuer_url", ""),
    )


def get_oauth_provider(db: Session, slug: str) -> OAuthProviderConfig | None:
    """Return the provider configured under `oauth.<slug>.*`, or None if no rows exist."""
    rows: dict[str, str] = {}
    for field in _FIELDS:
        value = _row(db, _key(slug, field))
        if value is not None:
            rows[field] = value
    if not rows:
        return None
    return _build(slug, rows)


def list_oauth_providers(db: Session) -> list[OAuthProviderConfig]:
    """Return every configured provider, sorted by slug."""
    all_rows = (
        db.query(AppConfig)
        .filter(AppConfig.key.like("oauth.%"))
        .all()
    )
    by_slug: dict[str, dict[str, str]] = {}
    for row in all_rows:
        # key shape: "oauth.<slug>.<field>"
        parts = row.key.split(".", 2)
        if len(parts) != 3:
            continue
        _, slug, field = parts
        if field not in _FIELDS:
            continue
        by_slug.setdefault(slug, {})[field] = row.value
    return [_build(slug, rows) for slug, rows in sorted(by_slug.items())]


def upsert_oauth_provider(db: Session, provider: OAuthProviderConfig) -> None:
    """Write every field of `provider` to app_config, overwriting existing rows."""
    values = {
        "name": provider.name,
        "enabled": "true" if provider.enabled else "false",
        "client_id": provider.client_id,
        "client_secret": provider.client_secret,
        "issuer_url": provider.issuer_url,
    }
    for field, value in values.items():
        key = _key(provider.slug, field)
        obj = db.get(AppConfig, key)
        if obj is None:
            db.add(AppConfig(key=key, value=value))
        else:
            obj.value = value
    db.commit()


def delete_oauth_provider(db: Session, slug: str) -> None:
    """Remove every `oauth.<slug>.*` row from app_config."""
    rows = (
        db.query(AppConfig)
        .filter(AppConfig.key.like(f"oauth.{slug}.%"))
        .all()
    )
    for row in rows:
        db.delete(row)
    db.commit()
