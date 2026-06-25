import asyncio
import ipaddress
import logging
import socket
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlparse

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


class _UserLike(Protocol):
    """Structural interface for create_access_token; any object with these
    attributes works — no need for a live ORM-instrumented User instance."""
    callsign: str
    is_admin: bool
    token_version: int


def create_access_token(
    user: "_UserLike",
    settings: Settings,
) -> str:
    """Mint a JWT for *user* signed with *settings*.

    The token carries ``sub`` (callsign), ``is_admin``, ``tv``
    (token_version for invalidation), and ``exp``.  The old ``role``
    claim is no longer included.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": user.callsign,
        "is_admin": user.is_admin,
        "exp": expire,
        # `tv` lets logout/role-change/delete invalidate every outstanding
        # token for this user by bumping users.token_version. See the
        # comparison in get_current_user.
        "tv": user.token_version,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError:
        return None


def _ssrf_guard_check_resolved_ips(host: str, infos: list) -> str:
    """Inspect the resolved IPs and raise ValueError if any is non-global.

    Returns the first resolved IP on success — callers pass this to
    backend.auth.dns_pin.pin_dns so the subsequent httpx fetch is
    locked to that exact IP (closes the DNS-rebinding TOCTOU between
    this check and the connect that follows).

    Split out from _ssrf_guard_discovery_url so the sync wrapper can keep
    calling getaddrinfo directly (for back-compat with existing callers
    and tests) while the async path can offload resolution to a thread.
    """
    if not infos:
        raise ValueError(f"OIDC issuer host {host} returned no addresses")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError(f"OIDC issuer host {host} resolves to a non-global IP ({ip})")
    return infos[0][4][0]


def _ssrf_guard_discovery_url(url: str) -> tuple[str, str]:
    """Raise ValueError if `url` is unsafe to fetch from the server side.

    Returns (hostname, resolved_ip). Callers pass these to
    `backend.auth.dns_pin.pin_dns` around the subsequent httpx fetch so
    DNS rebinding can't swap in a private IP between the check and the
    connect.

    `issuer_url` reaches this function from admin-supplied form input (and,
    pre-setup, from any unauthenticated POST to /api/setup/claim/start).
    Without a guard, an attacker can point us at http://169.254.169.254/
    (cloud metadata), http://127.0.0.1:<internal-port>, or any internal
    HTTP service and read the response body via _error_html on failure.

    This is the sync entry point. fetch_oidc_discovery offloads the DNS
    lookup to a thread via `_ssrf_guard_discovery_url_async` so a slow
    resolver doesn't stall the asyncio event loop.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"OIDC issuer URL must use https:// scheme (got {parsed.scheme or '(none)'})")
    host = parsed.hostname
    if not host:
        raise ValueError("OIDC issuer URL has no hostname")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"OIDC issuer host could not be resolved: {host}") from exc
    ip = _ssrf_guard_check_resolved_ips(host, infos)
    return host, ip


async def _ssrf_guard_discovery_url_async(url: str) -> tuple[str, str]:
    """Async-safe SSRF guard. Returns (hostname, resolved_ip).

    Use before EVERY httpx fetch of an attacker-influenceable URL — not
    just the issuer discovery URL but also the token/userinfo/jwks URLs
    that come from the discovery doc (a hostile discovery server can
    otherwise point those at internal addresses and bypass the guard
    entirely). Pass the returned (host, ip) into
    `backend.auth.dns_pin.pin_dns` so the subsequent httpx fetch is
    locked to the exact IP this guard verified — closing the DNS-
    rebinding TOCTOU.

    socket.getaddrinfo is blocking, so we offload to a thread; without
    that, a slow-resolving attacker hostname could stall the single
    uvicorn worker.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"OIDC issuer URL must use https:// scheme (got {parsed.scheme or '(none)'})")
    host = parsed.hostname
    if not host:
        raise ValueError("OIDC issuer URL has no hostname")
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except socket.gaierror as exc:
        raise ValueError(f"OIDC issuer host could not be resolved: {host}") from exc
    ip = _ssrf_guard_check_resolved_ips(host, infos)
    return host, ip


async def fetch_oidc_discovery(discovery_url: str) -> dict | None:
    """Fetch OIDC discovery document and return endpoint URLs, or None on failure."""
    try:
        host, ip = await _ssrf_guard_discovery_url_async(discovery_url)
    except ValueError as exc:
        logger.error("Rejecting unsafe OIDC discovery URL %s: %s", discovery_url, exc)
        return None
    try:
        # Pin the hostname to the exact IP the guard just verified, so the
        # TCP connect can't be redirected via DNS rebinding to an internal
        # address between the check and the fetch.
        from backend.auth.dns_pin import pin_dns

        with pin_dns(host, ip):
            async with httpx.AsyncClient() as client:
                response = await client.get(discovery_url, timeout=10)
                response.raise_for_status()
                return response.json()
    except Exception:
        logger.error("Failed to fetch OIDC discovery from %s", discovery_url)
        return None


# Module-level cache: discovery_url -> (fetched_at, discovery dict).
# A bounded TTL (1 h) plus a max-size cap stops a one-shot MITM during
# cache fill from permanently corrupting the process, and prevents an
# attacker who can spin up many distinct issuer_urls from exhausting
# memory through the cache.
_DISCOVERY_CACHE: dict[str, tuple[datetime, dict]] = {}
_DISCOVERY_CACHE_TTL = timedelta(hours=1)
_DISCOVERY_CACHE_MAX = 64


async def _get_discovery(discovery_url: str) -> dict | None:
    now = datetime.now(timezone.utc)
    cached = _DISCOVERY_CACHE.get(discovery_url)
    if cached is not None:
        fetched_at, doc = cached
        if now - fetched_at < _DISCOVERY_CACHE_TTL:
            # Move to MRU end so cap-eviction targets the least-recently-
            # used entry, not the oldest-by-insertion. Without this, an
            # attacker who can force enough discovery fetches to fill the
            # cap would evict the legit IdP every cycle.
            del _DISCOVERY_CACHE[discovery_url]
            _DISCOVERY_CACHE[discovery_url] = (fetched_at, doc)
            return doc
        del _DISCOVERY_CACHE[discovery_url]
    discovery = await fetch_oidc_discovery(discovery_url)
    if discovery is not None:
        if len(_DISCOVERY_CACHE) >= _DISCOVERY_CACHE_MAX:
            # Evict LRU (insertion order = LRU order once we touch on read).
            oldest = next(iter(_DISCOVERY_CACHE))
            del _DISCOVERY_CACHE[oldest]
        _DISCOVERY_CACHE[discovery_url] = (now, discovery)
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

    issuer = ""
    jwks_uri = ""
    if config.protocol == "oidc":
        discovery = await _get_discovery(config.discovery_url) if config.discovery_url else None
        if discovery is None:
            logger.warning("resolve_provider(%s): OIDC discovery failed", slug)
            return None
        authorize_url = discovery.get("authorization_endpoint", "")
        token_url = discovery.get("token_endpoint", "")
        userinfo_url = discovery.get("userinfo_endpoint", "")
        # Captured here so the callback can verify id_token against the
        # IdP's published JWKS without re-fetching discovery.
        issuer = discovery.get("issuer", "")
        jwks_uri = discovery.get("jwks_uri", "")
    else:
        authorize_url = config.authorize_url
        token_url = config.token_url
        userinfo_url = config.userinfo_url

    return {
        "authorize_url": authorize_url,
        "token_url": token_url,
        "userinfo_url": userinfo_url,
        "issuer": issuer,
        "jwks_uri": jwks_uri,
        "client_id": provider_settings.client_id,
        "client_secret": provider_settings.client_secret,
        "scopes": config.scopes,
        "label": config.label,
        "protocol": config.protocol,
        "extract_subject": config.extract_subject,
        "extract_name": config.extract_name,
        "extract_email": config.extract_email,
    }
