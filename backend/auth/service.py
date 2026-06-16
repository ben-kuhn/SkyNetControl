import logging
from datetime import datetime, timedelta, timezone

import httpx
from jose import JWTError, jwt

from backend.auth.providers import (
    FIXED_PROVIDERS,
    ProviderConfig,
    _normalise_issuer,
    _oidc_extract_email,
    _oidc_extract_name,
    _oidc_extract_subject,
)
from backend.config import Settings
from backend.config_mgmt.oauth import get_oauth_provider

logger = logging.getLogger(__name__)


def create_access_token(
    callsign: str,
    role: str,
    settings: Settings,
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": callsign,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError:
        return None


async def fetch_oidc_discovery(discovery_url: str) -> dict | None:
    """Fetch OIDC discovery document and return endpoint URLs, or None on failure."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(discovery_url, timeout=10)
            response.raise_for_status()
            return response.json()
    except Exception:
        logger.error("Failed to fetch OIDC discovery from %s", discovery_url)
        return None


# Module-level cache: discovery_url -> resolved discovery dict.
# Phase 2a accepts no TTL: cache lives for the process lifetime. OIDC
# providers rotate endpoints rarely enough that a restart suffices. If
# this becomes a problem the cache can grow a TTL or an invalidation hook
# tied to upsert_oauth_provider.
_DISCOVERY_CACHE: dict[str, dict] = {}


async def _get_discovery(discovery_url: str) -> dict | None:
    cached = _DISCOVERY_CACHE.get(discovery_url)
    if cached is not None:
        return cached
    discovery = await fetch_oidc_discovery(discovery_url)
    if discovery is not None:
        _DISCOVERY_CACHE[discovery_url] = discovery
    return discovery


async def resolve_provider(db, slug: str) -> dict | None:
    """Lazily resolve a single provider for a login flow.

    Reads the provider's credentials from the AppConfig table (Phase 1) for
    just this slug, merges with the static provider registry
    (FIXED_PROVIDERS) or builds a dynamic OIDC entry from the stored
    issuer_url, and — for OIDC — fetches or reads from the discovery cache.

    Returns the resolved auth-flow dict (with authorize/token/userinfo
    URLs filled in), or None if the provider is unknown, disabled,
    has no client_id, or — for OIDC — discovery failed.
    """
    # Single targeted read (5 PK gets via the Phase 1 accessor) instead of
    # the previous get_enabled_providers + build_providers pair, which each
    # ran a `SELECT * FROM app_config WHERE key LIKE 'oauth.%'` scan and
    # fetched every provider just to discard all but one.
    provider_settings = get_oauth_provider(db, slug)
    if provider_settings is None or not provider_settings.enabled or not provider_settings.client_id:
        return None

    config = FIXED_PROVIDERS.get(slug)
    if config is None:
        # Custom OIDC provider — build the ProviderConfig from the stored
        # issuer_url + display name.
        config = ProviderConfig(
            protocol="oidc",
            label=provider_settings.name or slug.title(),
            scopes="openid email profile",
            discovery_url=_normalise_issuer(provider_settings.issuer_url) if provider_settings.issuer_url else "",
            extract_subject=_oidc_extract_subject,
            extract_name=_oidc_extract_name,
            extract_email=_oidc_extract_email,
        )

    if config.protocol == "oidc":
        discovery = await _get_discovery(config.discovery_url) if config.discovery_url else None
        if discovery is None:
            logger.warning("resolve_provider(%s): OIDC discovery failed", slug)
            return None
        authorize_url = discovery.get("authorization_endpoint", "")
        token_url = discovery.get("token_endpoint", "")
        userinfo_url = discovery.get("userinfo_endpoint", "")
    else:
        authorize_url = config.authorize_url
        token_url = config.token_url
        userinfo_url = config.userinfo_url

    return {
        "authorize_url": authorize_url,
        "token_url": token_url,
        "userinfo_url": userinfo_url,
        "client_id": provider_settings.client_id,
        "client_secret": provider_settings.client_secret,
        "scopes": config.scopes,
        "label": config.label,
        "protocol": config.protocol,
        "extract_subject": config.extract_subject,
        "extract_name": config.extract_name,
        "extract_email": config.extract_email,
    }
