import asyncio
import secrets
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import Principal, get_db_session, get_settings, require_admin_or_recovery
from backend.auth.email import _send_email_sync
from backend.auth.providers import FIXED_PROVIDERS, _normalise_issuer
from backend.auth.service import _get_discovery
from backend.config import Settings
from backend.config_mgmt.oauth import get_oauth_provider
from backend.config_mgmt.smtp import SmtpConfig, get_smtp_config

test_router = APIRouter(prefix="/test", tags=["admin-test"])

_SESSION_TTL = timedelta(minutes=10)


@dataclass
class _TestSession:
    test_session_id: str
    state: str
    slug: str
    client_id: str
    client_secret: str = field(repr=False)
    issuer_url: str
    expires_at: datetime
    status: str = "pending"  # "pending" | "success" | "failed"
    error: str | None = None
    identity: dict | None = None
    # nonce + discovery-derived issuer/jwks for id_token verification on
    # the OIDC callback (empty strings for OAuth2-only providers).
    nonce: str = ""
    oidc_issuer: str = ""
    oidc_jwks_uri: str = ""


# Keyed by `state` — the unguessable OAuth state parameter used as the lookup key.
#
# This dict is process-local. The current NixOS deployment runs a single uvicorn
# worker, so the callback always lands in the worker that issued the state. A
# multi-worker deployment would need a shared store (Redis, DB row with TTL)
# because the provider's redirect could land in any worker.
_TEST_SESSIONS: dict[str, _TestSession] = {}


def _sweep_expired() -> None:
    """Drop expired entries from _TEST_SESSIONS.

    Sessions are otherwise cleaned up only when accessed by state via
    _get_live_session, so abandoned wizards would otherwise accumulate
    in memory. Called at the start of each new test-session creation —
    O(n) over the small in-flight set, which is fine at single-admin scale.
    """
    now = datetime.now(timezone.utc)
    expired = [state for state, s in _TEST_SESSIONS.items() if now >= s.expires_at]
    for state in expired:
        del _TEST_SESSIONS[state]


def _get_live_session(state: str) -> _TestSession | None:
    """Look up a session by state for callback handling.

    Returns None if missing, expired, or already resolved (single-use). The
    session itself stays in `_TEST_SESSIONS` after resolution so the result
    endpoint can still find it by `test_session_id` — `_get_live_session`'s
    pending-only filter is what makes replay impossible.
    """
    session = _TEST_SESSIONS.get(state)
    if session is None:
        return None
    if datetime.now(timezone.utc) >= session.expires_at:
        del _TEST_SESSIONS[state]
        return None
    if session.status != "pending":
        return None  # single-use: callback already resolved this session
    return session


# ---------------------------------------------------------------------------
# OAuth test: start
# ---------------------------------------------------------------------------


class OAuthTestStart(BaseModel):
    client_id: str
    client_secret: str
    issuer_url: str
    name: str


@test_router.post("/oauth/{slug}/start")
async def start_oauth_test(
    slug: str,
    body: OAuthTestStart,
    _: Principal = Depends(require_admin_or_recovery),
    app_settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db_session),
) -> dict:
    """Kick off a real OAuth test flow against unsaved credentials.

    Builds an authorize URL using the same registry logic as resolve_provider
    (FIXED_PROVIDERS for known slugs; OIDC discovery for custom slugs).
    Returns {test_session_id, authorize_url}.

    Stored-secret fallback: when body.client_secret is "" (the same sentinel
    the CRUD upsert uses to mean "preserve existing"), look up the stored
    provider's client_secret and use it. This lets an admin click "Test
    sign-in" on a saved row without re-typing the secret.
    """
    _sweep_expired()
    # Determine authorize URL via the same logic as resolve_provider
    provider_config = FIXED_PROVIDERS.get(slug)
    oidc_issuer = ""
    oidc_jwks_uri = ""
    is_oidc = False
    if provider_config is not None:
        if provider_config.protocol == "oidc":
            discovery = await _get_discovery(provider_config.discovery_url)
            if discovery is None:
                raise HTTPException(status_code=400, detail="oidc discovery failed")
            authorize_url = discovery.get("authorization_endpoint", "")
            oidc_issuer = discovery.get("issuer", "")
            oidc_jwks_uri = discovery.get("jwks_uri", "")
            is_oidc = True
        else:
            authorize_url = provider_config.authorize_url
        scopes = provider_config.scopes
    else:
        # Custom OIDC provider
        if not body.issuer_url:
            raise HTTPException(status_code=400, detail="issuer_url is required for custom OIDC providers")
        discovery_url = _normalise_issuer(body.issuer_url)
        discovery = await _get_discovery(discovery_url)
        if discovery is None:
            raise HTTPException(status_code=400, detail="oidc discovery failed")
        authorize_url = discovery.get("authorization_endpoint", "")
        oidc_issuer = discovery.get("issuer", "")
        oidc_jwks_uri = discovery.get("jwks_uri", "")
        is_oidc = True
        scopes = "openid email profile"

    # Stored-secret fallback (mirrors SMTP test): empty client_secret means
    # "use the saved one." Required for the Test-sign-in button on saved rows
    # where the frontend never receives the real secret.
    client_secret = body.client_secret
    if client_secret == "":
        stored = get_oauth_provider(db, slug)
        if stored is None or not stored.client_secret:
            raise HTTPException(
                status_code=400,
                detail="client_secret is required (no stored secret to fall back to)",
            )
        client_secret = stored.client_secret

    state = secrets.token_urlsafe(32)
    test_session_id = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32) if is_oidc else ""

    # Test callbacks land on the same URI as everyday sign-in
    # (/api/auth/callback/{slug}) — admins only need to register one
    # redirect URI at the IdP. Dispatch in the unified callback uses the
    # in-memory _TEST_SESSIONS lookup to tell test traffic from real
    # sign-in. See try_complete_oauth_test().
    redirect_uri = f"{app_settings.app_base_url}/api/auth/callback/{slug}"
    params = {
        "client_id": body.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state,
    }
    if nonce:
        params["nonce"] = nonce
    full_authorize_url = f"{authorize_url}?{urllib.parse.urlencode(params)}"

    session = _TestSession(
        test_session_id=test_session_id,
        state=state,
        slug=slug,
        client_id=body.client_id,
        client_secret=client_secret,
        issuer_url=body.issuer_url,
        expires_at=datetime.now(timezone.utc) + _SESSION_TTL,
        nonce=nonce,
        oidc_issuer=oidc_issuer,
        oidc_jwks_uri=oidc_jwks_uri,
    )
    _TEST_SESSIONS[state] = session

    return {"test_session_id": test_session_id, "authorize_url": full_authorize_url}


# ---------------------------------------------------------------------------
# OAuth test: callback dispatch invoked from /api/auth/callback/{slug}
# ---------------------------------------------------------------------------


async def try_complete_oauth_test(
    state: str,
    code: str,
    error: str,
    app_settings: Settings,
) -> HTMLResponse | None:
    """If `state` matches a live test session, finish the test and return the autoclose page.

    Returns None when no test session is in flight for this state — the caller
    should fall through to the normal sign-in path. Like try_complete_setup,
    the in-memory _TEST_SESSIONS lookup IS the authentication here; state is
    32 bytes of secrets.token_urlsafe so a collision with a real sign-in is
    negligible.
    """
    session = _get_live_session(state)
    if session is None:
        return None

    if error:
        session.status = "failed"
        session.error = error
        return _autoclose_html(session.test_session_id, "failed", app_settings.app_base_url)

    # Determine token_url and userinfo_url
    provider_config = FIXED_PROVIDERS.get(session.slug)
    if provider_config is not None:
        if provider_config.protocol == "oidc":
            discovery = await _get_discovery(provider_config.discovery_url)
            if discovery is None:
                session.status = "failed"
                session.error = "oidc discovery failed during token exchange"
                return _autoclose_html(session.test_session_id, "failed", app_settings.app_base_url)
            token_url = discovery.get("token_endpoint", "")
            userinfo_url = discovery.get("userinfo_endpoint", "")
        else:
            token_url = provider_config.token_url
            userinfo_url = provider_config.userinfo_url
    else:
        # Custom OIDC
        discovery_url = _normalise_issuer(session.issuer_url)
        discovery = await _get_discovery(discovery_url)
        if discovery is None:
            session.status = "failed"
            session.error = "oidc discovery failed during token exchange"
            return _autoclose_html(session.test_session_id, "failed", app_settings.app_base_url)
        token_url = discovery.get("token_endpoint", "")
        userinfo_url = discovery.get("userinfo_endpoint", "")

    # Must match the redirect_uri sent in /start — see start_oauth_test().
    redirect_uri = f"{app_settings.app_base_url}/api/auth/callback/{session.slug}"

    # SSRF-guard the discovery-derived URLs (token/userinfo). Admin-gated,
    # but defense-in-depth — an admin tricked into testing a hostile
    # OIDC provider shouldn't grant that provider SSRF. Also pin DNS so
    # the connect can't be redirected via rebinding between check and fetch.
    import contextlib as _ctx

    from backend.auth.dns_pin import pin_dns
    from backend.auth.service import _ssrf_guard_discovery_url_async

    _pins: dict[str, str] = {}
    for url in (token_url, userinfo_url):
        try:
            _host, _ip = await _ssrf_guard_discovery_url_async(url)
            _pins[_host] = _ip
        except ValueError as exc:
            session.status = "failed"
            session.error = f"Provider URL refused by SSRF guard: {exc}"
            return _autoclose_html(session.test_session_id, "failed", app_settings.app_base_url)

    try:
        with _ctx.ExitStack() as _stack:
            for _host, _ip in _pins.items():
                _stack.enter_context(pin_dns(_host, _ip))
            async with httpx.AsyncClient() as client:
                token_response = await client.post(
                    token_url,
                    data={
                        "client_id": session.client_id,
                        "client_secret": session.client_secret,
                        "code": code,
                        "grant_type": "authorization_code",
                        "redirect_uri": redirect_uri,
                    },
                    headers={"Accept": "application/json"},
                )
                token_data = token_response.json()
                access_token = token_data.get("access_token", "")

                if not access_token:
                    err_desc = (
                        token_data.get("error_description")
                        or token_data.get("error")
                        or "no access_token in response"
                    )
                    session.status = "failed"
                    session.error = err_desc
                    return _autoclose_html(
                        session.test_session_id, "failed", app_settings.app_base_url
                    )

                # Verify id_token for OIDC providers — same protection as the
                # everyday sign-in path. Skipped for OAuth2-only providers.
                if session.oidc_jwks_uri:
                    id_token_value = token_data.get("id_token", "")
                    if not id_token_value:
                        session.status = "failed"
                        session.error = "provider did not return id_token"
                        return _autoclose_html(
                            session.test_session_id, "failed", app_settings.app_base_url
                        )
                    from backend.auth.oidc_verify import verify_id_token

                    claims = await verify_id_token(
                        id_token_value,
                        expected_issuer=session.oidc_issuer,
                        expected_audience=session.client_id,
                        expected_nonce=session.nonce,
                        jwks_uri=session.oidc_jwks_uri,
                        access_token=access_token,
                    )
                    if claims is None:
                        session.status = "failed"
                        session.error = "id_token verification failed"
                        return _autoclose_html(
                            session.test_session_id, "failed", app_settings.app_base_url
                        )

                userinfo_response = await client.get(
                    userinfo_url,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                userinfo = userinfo_response.json()
    except Exception as exc:
        session.status = "failed"
        session.error = str(exc)
        return _autoclose_html(session.test_session_id, "failed", app_settings.app_base_url)

    # Strip identity to display-safe fields only
    identity = {k: userinfo[k] for k in ("sub", "email", "name") if k in userinfo}

    session.status = "success"
    session.identity = identity
    return _autoclose_html(session.test_session_id, "success", app_settings.app_base_url)


def _autoclose_html(test_session_id: str, status: str, target_origin: str) -> HTMLResponse:
    # target_origin scopes the postMessage to the configured app origin
    # (per MDN's recommendation against using "*"); the opener — the
    # admin Config page — runs on the same origin, so it's the only
    # listener we want to reach.
    html = f"""<!DOCTYPE html>
<html>
<head><title>OAuth Test</title></head>
<body>
<script>
  window.opener && window.opener.postMessage(
    {{type: "oauth_test", test_session_id: {test_session_id!r}, status: {status!r}}},
    {target_origin!r}
  );
  window.close();
</script>
<p>You can close this window.</p>
</body>
</html>"""
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# OAuth test: result (admin auth required)
# ---------------------------------------------------------------------------


@test_router.get("/oauth/{test_session_id}/result")
def get_oauth_test_result(
    test_session_id: str,
    _: Principal = Depends(require_admin_or_recovery),
) -> dict:
    """Poll fallback — returns the current status of a test session by test_session_id."""
    for session in _TEST_SESSIONS.values():
        if session.test_session_id == test_session_id:
            result: dict = {"status": session.status}
            if session.error is not None:
                result["error"] = session.error
            if session.identity is not None:
                result["identity"] = session.identity
            return result
    raise HTTPException(status_code=404, detail="Test session not found")


# ---------------------------------------------------------------------------
# SMTP test
# ---------------------------------------------------------------------------


class SmtpTestBody(BaseModel):
    host: str
    port: int
    username: str
    password: str
    from_address: str
    use_tls: bool
    to_address: str


@test_router.post("/groupsio")
async def groupsio_test(
    _: Principal = Depends(require_admin_or_recovery),
    db: Session = Depends(get_db_session),
) -> dict:
    """Post a benign test message to the configured groups.io group.

    Uses the saved api_key and group_name (the frontend never receives the
    raw api_key — it's masked as '***'), so the operator must Save before
    Test. Returns {ok, error} so the UI can surface the actual groups.io
    response body on failure.
    """
    from backend.config_mgmt.service import get_config_value
    from backend.integrations.delivery.backends.groupsio import GroupsIoBackend

    api_key = get_config_value(db, "delivery.groupsio.api_key", "")
    group_name = get_config_value(db, "delivery.groupsio.group_name", "")

    result = await asyncio.to_thread(
        GroupsIoBackend().send,
        "[SkyNetControl] Test Message",
        "Test Message. Sorry for the noise, please disregard.",
        {"api_key": api_key, "group_name": group_name},
    )
    if result.success:
        return {"ok": True}
    return {"ok": False, "error": result.error}


@test_router.post("/smtp")
async def smtp_test(
    body: SmtpTestBody,
    _: Principal = Depends(require_admin_or_recovery),
    db: Session = Depends(get_db_session),
) -> dict:
    """Send a test email using the supplied (unsaved) SMTP settings.

    password="" means "use the stored password".
    password="-" means "send without auth".
    """
    password = body.password
    if password == "":
        stored = get_smtp_config(db)
        password = stored.password if stored else ""
    elif password == "-":
        password = ""

    smtp_cfg = SmtpConfig(
        host=body.host,
        port=body.port,
        username="" if body.password == "-" else body.username,
        password=password,
        from_address=body.from_address,
        use_tls=body.use_tls,
    )

    try:
        await asyncio.to_thread(
            _send_email_sync,
            smtp_cfg,
            body.to_address,
            "[SkyNetControl] SMTP test",
            "This is a test message from SkyNetControl. If you received this, SMTP is working correctly.",
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": True}
