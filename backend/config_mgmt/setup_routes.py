import secrets
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape as html_escape

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, get_settings
from backend.auth.recovery import decode_recovery_token
from backend.auth.models import User, UserRole
from backend.auth.providers import (
    FIXED_PROVIDERS,
    _normalise_issuer,
    _oidc_extract_email,
    _oidc_extract_name,
    _oidc_extract_subject,
)
from backend.auth.service import _get_discovery, create_access_token
from backend.config import Settings
from backend.config_mgmt.oauth import OAuthProviderConfig, _check_slug, upsert_oauth_provider
from backend.config_mgmt.service import set_config_value
from backend.config_mgmt.setup_state import is_setup_completed, mark_setup_completed
from backend.config_mgmt.smtp import SmtpConfig, upsert_smtp_config
from backend.config_mgmt.smtp_routes import SmtpUpsert

setup_router = APIRouter(tags=["setup"])

_SESSION_TTL = timedelta(minutes=30)


@dataclass
class _SetupSession:
    state: str
    # Step 1: net basics
    default_net_control: str
    net_address: str
    app_base_url: str
    # Step 2: oauth provider (exactly one)
    oauth_slug: str
    oauth_name: str
    oauth_client_id: str
    oauth_client_secret: str = field(repr=False)
    oauth_issuer_url: str  # empty for non-OIDC
    # Step 3: smtp (optional)
    smtp: SmtpConfig | None
    # Bookkeeping
    expires_at: datetime


_SETUP_SESSIONS: dict[str, _SetupSession] = {}  # keyed by `state`


class SetupClaimStart(BaseModel):
    default_net_control: str
    net_address: str
    app_base_url: str
    oauth_slug: str
    oauth_name: str
    oauth_client_id: str
    oauth_client_secret: str
    oauth_issuer_url: str = ""
    smtp: SmtpUpsert | None = None


def _error_html(title: str, message: str) -> HTMLResponse:
    # Both args may carry provider-supplied strings (the OAuth `error` query
    # param is attacker-influenceable). Escape before embedding in HTML.
    safe_title = html_escape(title)
    safe_message = html_escape(message)
    body = f"""<!DOCTYPE html>
<html>
<head><title>{safe_title}</title></head>
<body>
<h1>{safe_title}</h1>
<p>{safe_message}</p>
<p><a href="/">Back to setup wizard</a></p>
</body>
</html>"""
    return HTMLResponse(content=body, status_code=400)


# ---------------------------------------------------------------------------
# GET /api/setup/status
# ---------------------------------------------------------------------------


@setup_router.get("/status")
def setup_status(
    request: Request,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Always returns {setup_completed, recovery_mode}. No auth required.

    recovery_mode is true iff the request carries a valid recovery cookie.
    """
    cookie = request.cookies.get("recovery_token")
    in_recovery = False
    if cookie:
        in_recovery = decode_recovery_token(cookie, settings) is not None
    return {"setup_completed": is_setup_completed(db), "recovery_mode": in_recovery}


# ---------------------------------------------------------------------------
# POST /api/setup/claim/start
# ---------------------------------------------------------------------------


@setup_router.post("/claim/start")
async def setup_claim_start(
    body: SetupClaimStart,
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
) -> dict:
    """Capture wizard inputs, build the OAuth authorize URL, and return it.

    Returns 410 if setup is already complete.
    """
    if is_setup_completed(db):
        raise HTTPException(status_code=410, detail="Setup already completed")

    # Validate slug
    try:
        _check_slug(body.oauth_slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Non-empty field validation
    errors = []
    if not body.default_net_control:
        errors.append("default_net_control is required")
    if not body.net_address:
        errors.append("net_address is required")
    if not body.app_base_url:
        errors.append("app_base_url is required")
    if not body.oauth_client_id:
        errors.append("oauth_client_id is required")
    if not body.oauth_client_secret:
        errors.append("oauth_client_secret is required")
    if body.oauth_slug not in FIXED_PROVIDERS and not body.oauth_issuer_url:
        errors.append("oauth_issuer_url is required for custom OIDC providers")
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    # Build authorize URL — mirror start_oauth_test
    provider_config = FIXED_PROVIDERS.get(body.oauth_slug)
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
        discovery_url = _normalise_issuer(body.oauth_issuer_url)
        discovery = await _get_discovery(discovery_url)
        if discovery is None:
            raise HTTPException(status_code=400, detail="oidc discovery failed")
        authorize_url = discovery.get("authorization_endpoint", "")
        scopes = "openid email profile"

    state = secrets.token_urlsafe(32)
    redirect_uri = f"{body.app_base_url}/api/setup/claim/callback"
    params = {
        "client_id": body.oauth_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state,
    }
    full_authorize_url = f"{authorize_url}?{urllib.parse.urlencode(params)}"

    # Convert smtp body to SmtpConfig if present
    smtp_cfg: SmtpConfig | None = None
    if body.smtp is not None:
        smtp_cfg = SmtpConfig(
            host=body.smtp.host,
            port=body.smtp.port,
            username=body.smtp.username,
            password=body.smtp.password,
            from_address=body.smtp.from_address,
            use_tls=body.smtp.use_tls,
        )

    session = _SetupSession(
        state=state,
        default_net_control=body.default_net_control,
        net_address=body.net_address,
        app_base_url=body.app_base_url,
        oauth_slug=body.oauth_slug,
        oauth_name=body.oauth_name,
        oauth_client_id=body.oauth_client_id,
        oauth_client_secret=body.oauth_client_secret,
        oauth_issuer_url=body.oauth_issuer_url,
        smtp=smtp_cfg,
        expires_at=datetime.now(timezone.utc) + _SESSION_TTL,
    )
    _SETUP_SESSIONS[state] = session

    return {"authorize_url": full_authorize_url}


# ---------------------------------------------------------------------------
# GET /api/setup/claim/callback
# ---------------------------------------------------------------------------


@setup_router.get("/claim/callback")
async def setup_claim_callback(
    state: str = "",
    code: str = "",
    error: str = "",
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
):
    """State-validated OAuth callback. No auth — secured by unguessable state token.

    On success, commits all wizard inputs, creates the first admin user,
    issues a JWT cookie, and redirects to /.
    Returns 410 if setup is already complete.
    """
    if is_setup_completed(db):
        raise HTTPException(status_code=410, detail="Setup already completed")

    # Look up and validate session
    session = _SETUP_SESSIONS.get(state)
    if session is None:
        raise HTTPException(status_code=404, detail="Setup session not found or expired")
    if datetime.now(timezone.utc) >= session.expires_at:
        del _SETUP_SESSIONS[state]
        raise HTTPException(status_code=404, detail="Setup session not found or expired")

    # Provider sent an error
    if error:
        del _SETUP_SESSIONS[state]
        return _error_html("OAuth Error", f"The OAuth provider returned an error: {error}")

    # Determine token/userinfo URLs
    slug = session.oauth_slug
    provider_config = FIXED_PROVIDERS.get(slug)
    if provider_config is not None:
        if provider_config.protocol == "oidc":
            discovery = await _get_discovery(provider_config.discovery_url)
            if discovery is None:
                del _SETUP_SESSIONS[state]
                return _error_html("OIDC Discovery Failed", "Could not fetch OIDC discovery document.")
            token_url = discovery.get("token_endpoint", "")
            userinfo_url = discovery.get("userinfo_endpoint", "")
        else:
            token_url = provider_config.token_url
            userinfo_url = provider_config.userinfo_url
        extract_subject = provider_config.extract_subject
        extract_name = provider_config.extract_name
        extract_email = provider_config.extract_email
    else:
        discovery_url = _normalise_issuer(session.oauth_issuer_url)
        discovery = await _get_discovery(discovery_url)
        if discovery is None:
            del _SETUP_SESSIONS[state]
            return _error_html("OIDC Discovery Failed", "Could not fetch OIDC discovery document.")
        token_url = discovery.get("token_endpoint", "")
        userinfo_url = discovery.get("userinfo_endpoint", "")
        extract_subject = _oidc_extract_subject
        extract_name = _oidc_extract_name
        extract_email = _oidc_extract_email

    redirect_uri = f"{session.app_base_url}/api/setup/claim/callback"

    try:
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": session.oauth_client_id,
                    "client_secret": session.oauth_client_secret,
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
                del _SETUP_SESSIONS[state]
                return _error_html("Token Exchange Failed", f"Could not obtain access token: {err_desc}")

            userinfo_response = await client.get(
                userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo = userinfo_response.json()
    except Exception as exc:
        del _SETUP_SESSIONS[state]
        return _error_html("OAuth Error", f"An error occurred during the OAuth flow: {exc}")

    extracted_sub = extract_subject(userinfo)
    extracted_name = extract_name(userinfo)
    extracted_email = extract_email(userinfo)

    # Commit all wizard inputs. The Phase 1 accessors commit internally,
    # so this is a sequence of commits rather than one atomic transaction.
    # Retry safety: every accessor is overwrite-with-same-value idempotent,
    # and the User row is upserted (db.merge) so a crash between the user
    # commit and mark_setup_completed doesn't leave a callsign-PK landmine.
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(
            slug=session.oauth_slug,
            name=session.oauth_name,
            enabled=True,
            client_id=session.oauth_client_id,
            client_secret=session.oauth_client_secret,
            issuer_url=session.oauth_issuer_url,
        ),
    )
    if session.smtp is not None:
        upsert_smtp_config(db, session.smtp)
    # app_base_url is also read from Settings.app_base_url today (env). Phase 5
    # removes the env path; until then, the AppConfig row is authoritative for
    # the wizard / Config page but the env override still wins at startup.
    set_config_value(db, "default_net_control", session.default_net_control)
    set_config_value(db, "net_address", session.net_address)
    set_config_value(db, "app_base_url", session.app_base_url)
    db.merge(
        User(
            callsign=session.default_net_control,
            oidc_subject=f"{session.oauth_slug}:{extracted_sub}",
            name=extracted_name,
            email=extracted_email,
            role=UserRole.ADMIN,
        )
    )
    db.commit()
    mark_setup_completed(db)

    # Issue JWT cookie and redirect
    jwt_token = create_access_token(session.default_net_control, "admin", app_settings)
    is_secure = session.app_base_url.startswith("https://")
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="access_token",
        value=jwt_token,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=app_settings.jwt_expire_minutes * 60,
    )

    # Single-use: remove the session
    del _SETUP_SESSIONS[state]

    return response
