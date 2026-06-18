"""OIDC id_token signature and claim verification.

Without this, the userinfo response was the only thing the backend
authenticated against — and userinfo is reached via an access_token the
IdP minted for us, so it's already trustworthy *if* the token came from
the right party. The audit's concern was that we never verify the
id_token alongside, so a code path that ever fed an attacker-controlled
access_token (e.g. via a future endpoint, a cache-poisoning attack on
the discovery doc, or a misconfigured provider) would have no second
line of defense. Verifying id_token closes that gap and pins the
authentication to the IdP key + the originating sign-in (via nonce).

Scope:
- OIDC providers (Google, Microsoft, custom OIDC) only. OAuth2-only
  providers (GitHub, Discord, Facebook) don't issue id_tokens.
- All three OAuth-completing handlers — auth/routes callback,
  setup_routes try_complete_setup, test_routes oauth_test_callback —
  call into here.
- The nonce is generated at authorize-start time and carried across the
  redirect via the existing per-flow storage: oauth_state cookie for
  everyday sign-in, _SetupSession for the wizard, _TestSession for the
  admin test flow.
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx
from jose import JWTError, jwt

from backend.auth.service import _ssrf_guard_discovery_url_async

logger = logging.getLogger(__name__)

# Module-level JWKS cache: jwks_uri -> (fetched_at, keys list).
# Mirrors _DISCOVERY_CACHE behaviour — bounded TTL plus a size cap so one
# MITM during cache-fill doesn't permanently control the process, and a
# pre-auth attacker can't exhaust memory by spinning up many jwks_uris.
_JWKS_CACHE: dict[str, tuple[datetime, list[dict]]] = {}
_JWKS_CACHE_TTL = timedelta(hours=1)
_JWKS_CACHE_MAX = 64

# Algorithms accepted from JWKS. Locked to RSA/EC signing algorithms —
# explicitly excludes "none" (signature-bypass) and HMAC (which would
# require a shared secret and shouldn't appear on a public OIDC JWKS).
_ALLOWED_ALGS = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"}


async def _fetch_jwks(jwks_uri: str) -> list[dict] | None:
    """Fetch a JWKS doc and return its `keys` array, or None on failure.

    Same SSRF guard as discovery — jwks_uri is sourced from a discovery
    doc which we don't fully validate, so an attacker who poisoned the
    discovery cache could otherwise point us at an internal URL.
    """
    try:
        await _ssrf_guard_discovery_url_async(jwks_uri)
    except ValueError as exc:
        logger.error("Rejecting unsafe JWKS URI %s: %s", jwks_uri, exc)
        return None
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(jwks_uri, timeout=10)
            response.raise_for_status()
            doc = response.json()
            keys = doc.get("keys")
            if not isinstance(keys, list):
                return None
            return keys
    except Exception:
        logger.error("Failed to fetch JWKS from %s", jwks_uri)
        return None


async def _get_jwks(jwks_uri: str) -> list[dict] | None:
    now = datetime.now(timezone.utc)
    cached = _JWKS_CACHE.get(jwks_uri)
    if cached is not None:
        fetched_at, keys = cached
        if now - fetched_at < _JWKS_CACHE_TTL:
            # LRU touch (see _get_discovery): otherwise an attacker
            # rotating distinct jwks_uris evicts the legit entry.
            del _JWKS_CACHE[jwks_uri]
            _JWKS_CACHE[jwks_uri] = (fetched_at, keys)
            return keys
        del _JWKS_CACHE[jwks_uri]
    keys = await _fetch_jwks(jwks_uri)
    if keys is not None:
        if len(_JWKS_CACHE) >= _JWKS_CACHE_MAX:
            oldest = next(iter(_JWKS_CACHE))
            del _JWKS_CACHE[oldest]
        _JWKS_CACHE[jwks_uri] = (now, keys)
    return keys


async def verify_id_token(
    id_token: str,
    *,
    expected_issuer: str,
    expected_audience: str,
    expected_nonce: str,
    jwks_uri: str,
) -> dict | None:
    """Return verified claims, or None if verification fails.

    Validates: signature against the JWKS key matching `kid`, `iss` ==
    expected_issuer, `aud` == expected_audience, `exp` in future,
    `nonce` == expected_nonce. Algorithm restricted to the asymmetric
    set in _ALLOWED_ALGS — "none" and HMAC are rejected outright to
    prevent algorithm-confusion / signature-stripping attacks.

    Returning None (not raising) lets callers decide whether to fall
    back, log, or reject; the function logs the specific failure mode
    server-side so production has signal.
    """
    if not id_token or not jwks_uri:
        logger.warning("verify_id_token: missing id_token or jwks_uri")
        return None

    try:
        header = jwt.get_unverified_header(id_token)
    except JWTError as exc:
        logger.warning("verify_id_token: malformed token header: %s", exc)
        return None

    kid = header.get("kid")
    alg = header.get("alg")
    if alg not in _ALLOWED_ALGS:
        logger.warning("verify_id_token: rejected algorithm %r", alg)
        return None

    keys = await _get_jwks(jwks_uri)
    if not keys:
        logger.warning("verify_id_token: no JWKS keys available from %s", jwks_uri)
        return None

    # Match on kid when the token carries one; otherwise fall back to
    # the single-key case (some IdPs publish exactly one key without kid).
    if kid is not None:
        matching = [k for k in keys if k.get("kid") == kid]
    else:
        matching = keys
    if not matching:
        logger.warning("verify_id_token: no JWKS key matches kid=%r", kid)
        return None

    last_exc: Exception | None = None
    for key in matching:
        try:
            claims = jwt.decode(
                id_token,
                key,
                algorithms=[alg],
                audience=expected_audience,
                issuer=expected_issuer,
                # python-jose validates exp/nbf/iat by default; aud/iss
                # are validated when the arguments are passed.
            )
        except JWTError as exc:
            last_exc = exc
            continue
        # python-jose doesn't validate nonce — do it manually so a
        # replayed id_token from a prior sign-in can't be accepted.
        if expected_nonce:
            if claims.get("nonce") != expected_nonce:
                logger.warning(
                    "verify_id_token: nonce mismatch (got %r, expected %r)",
                    claims.get("nonce"),
                    expected_nonce,
                )
                return None
        return claims

    logger.warning("verify_id_token: signature/claim verification failed: %s", last_exc)
    return None


def reset_for_tests() -> None:
    """Drop cached JWKS keys. Tests that mock _fetch_jwks rely on a clean
    cache so the mock actually runs."""
    _JWKS_CACHE.clear()
