from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.auth.oidc_slug import RESERVED_SLUGS, validate_slug
from backend.config_mgmt.models import AppConfig


_FIXED_SLUGS = RESERVED_SLUGS - {"oidc"}  # all fixed providers; "oidc" stays reserved


def _check_slug(slug: str) -> None:
    # Fixed-provider slugs must round-trip through storage (the migration
    # writes them; the wizard edits them). They are in RESERVED_SLUGS only to
    # block user-chosen OIDC slugs from colliding — so we bypass validate_slug
    # here and accept them. Note: this bypass also skips the format regex; if
    # validate_slug ever grows additional checks that should apply to fixed
    # slugs too (e.g. a length cap), this branch must be revisited.
    if slug in _FIXED_SLUGS:
        return
    err = validate_slug(slug)
    if err is not None:
        raise ValueError(f"invalid OAuth provider slug {slug!r}: {err}")


@dataclass(frozen=True)
class OAuthProviderConfig:
    slug: str
    name: str
    enabled: bool
    client_id: str
    client_secret: str = field(repr=False)
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
    for field_name in _FIELDS:
        value = _row(db, _key(slug, field_name))
        if value is not None:
            rows[field_name] = value
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
        _, slug, field_name = parts
        if field_name not in _FIELDS:
            continue
        by_slug.setdefault(slug, {})[field_name] = row.value
    return [_build(slug, rows) for slug, rows in sorted(by_slug.items())]


def upsert_oauth_provider(db: Session, provider: OAuthProviderConfig) -> None:
    """Write every field of `provider` to app_config, overwriting existing rows.

    Raises ValueError if the slug fails `_check_slug` — slugs become parts
    of LIKE patterns in `delete_oauth_provider` / `list_oauth_providers`, so
    they must match the existing OIDC-slug whitelist.
    """
    _check_slug(provider.slug)
    values = {
        "name": provider.name,
        "enabled": "true" if provider.enabled else "false",
        "client_id": provider.client_id,
        "client_secret": provider.client_secret,
        "issuer_url": provider.issuer_url,
    }
    for field_name, value in values.items():
        key = _key(provider.slug, field_name)
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
