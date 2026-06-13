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

from backend.auth.dependencies import get_db_session, require_role
from backend.auth.models import User, UserRole
from backend.auth.providers import FIXED_PROVIDERS, _normalise_issuer
from backend.auth.service import _get_discovery
from backend.config_mgmt.smtp import SmtpConfig, get_smtp_config
from backend.auth.email import _send_email_sync
from backend.auth.dependencies import get_settings
from backend.config import Settings

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


# Keyed by `state` — the unguessable OAuth state parameter used as the lookup key.
_TEST_SESSIONS: dict[str, _TestSession] = {}


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
    _: User = Depends(require_role(UserRole.ADMIN)),
    app_settings: Settings = Depends(get_settings),
) -> dict:
    """Kick off a real OAuth test flow against unsaved credentials.

    Builds an authorize URL using the same registry logic as resolve_provider
    (FIXED_PROVIDERS for known slugs; OIDC discovery for custom slugs).
    Returns {test_session_id, authorize_url}.
    """
    # Determine authorize URL via the same logic as resolve_provider
    provider_config = FIXED_PROVIDERS.get(slug)
    if provider_config is not None:
        if provider_config.protocol == "oidc":
            discovery = await _get_discovery(provider_config.discovery_url)
            if discovery is None:
                raise HTTPException(status_code=400, detail="oidc discovery failed")
            authorize_url = discovery.get("authorization_endpoint", "")
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
        scopes = "openid email profile"

    state = secrets.token_urlsafe(32)
    test_session_id = secrets.token_urlsafe(32)

    redirect_uri = f"{app_settings.app_base_url}/api/admin/test/oauth/callback"
    params = {
        "client_id": body.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state,
    }
    full_authorize_url = f"{authorize_url}?{urllib.parse.urlencode(params)}"

    session = _TestSession(
        test_session_id=test_session_id,
        state=state,
        slug=slug,
        client_id=body.client_id,
        client_secret=body.client_secret,
        issuer_url=body.issuer_url,
        expires_at=datetime.now(timezone.utc) + _SESSION_TTL,
    )
    _TEST_SESSIONS[state] = session

    return {"test_session_id": test_session_id, "authorize_url": full_authorize_url}


# ---------------------------------------------------------------------------
# OAuth test: callback (no auth — security via unguessable state)
# ---------------------------------------------------------------------------


@test_router.get("/oauth/callback")
async def oauth_test_callback(
    state: str = "",
    code: str = "",
    error: str = "",
    app_settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Handle the OAuth provider redirect. No auth — secured by the unguessable state token."""
    session = _get_live_session(state)
    if session is None:
        raise HTTPException(status_code=404, detail="Test session not found or expired")

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

    redirect_uri = f"{app_settings.app_base_url}/api/admin/test/oauth/callback"

    try:
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
                    token_data.get("error_description") or token_data.get("error") or "no access_token in response"
                )
                session.status = "failed"
                session.error = err_desc
                return _autoclose_html(session.test_session_id, "failed", app_settings.app_base_url)

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
    _: User = Depends(require_role(UserRole.ADMIN)),
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


@test_router.post("/smtp")
async def smtp_test(
    body: SmtpTestBody,
    _: User = Depends(require_role(UserRole.ADMIN)),
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
