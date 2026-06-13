import logging
from datetime import datetime, timedelta, timezone

import httpx
from jose import JWTError, jwt

from backend.auth.providers import build_providers, get_enabled_providers
from backend.config import Settings

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

    Reads the provider's credentials from the AppConfig table (Phase 1),
    merges with the static provider registry (FIXED_PROVIDERS or the
    dynamic OIDC entry from build_providers), and — for OIDC — fetches
    or reads from the discovery cache.

    Returns the resolved auth-flow dict (with authorize/token/userinfo
    URLs filled in), or None if the provider is unknown, disabled,
    has no client_id, or — for OIDC — discovery failed.
    """
    enabled = get_enabled_providers(db)
    provider_settings = enabled.get(slug)
    if provider_settings is None:
        return None

    registry = build_providers(db)
    config = registry.get(slug)
    if config is None:
        return None

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
