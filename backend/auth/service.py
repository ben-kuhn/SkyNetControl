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


async def init_providers(settings: Settings) -> dict[str, dict]:
    """Initialize all enabled auth providers. Returns dict of provider_name -> resolved config.

    Each resolved config contains: authorize_url, token_url, userinfo_url,
    client_id, client_secret, scopes, label, protocol, and the provider's
    extract_* functions from the registry.

    Raises RuntimeError if no providers could be initialized.
    """
    enabled = get_enabled_providers(settings)
    if not enabled:
        raise RuntimeError("No auth providers are enabled. Set at least one SKYNET_AUTH_*_ENABLED=true.")

    registry = build_providers(settings)
    resolved = {}
    for name, provider_settings in enabled.items():
        config = registry[name]

        if config.protocol == "oidc":
            # Dynamic OIDC entries already have discovery_url baked in by
            # build_providers; fixed OIDC entries (google, microsoft) too.
            discovery_url = config.discovery_url

            discovery = await fetch_oidc_discovery(discovery_url)
            if discovery is None:
                logger.warning("Skipping provider %s — OIDC discovery failed", name)
                continue

            authorize_url = discovery.get("authorization_endpoint", "")
            token_url = discovery.get("token_endpoint", "")
            userinfo_url = discovery.get("userinfo_endpoint", "")
        else:
            authorize_url = config.authorize_url
            token_url = config.token_url
            userinfo_url = config.userinfo_url

        resolved[name] = {
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

    if not resolved:
        raise RuntimeError("No auth providers could be initialized. Check provider configuration and connectivity.")

    logger.info("Initialized auth providers: %s", ", ".join(resolved.keys()))
    return resolved
