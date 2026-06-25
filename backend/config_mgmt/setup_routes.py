import logging
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
from backend.auth.rate_limit import rate_limit
from backend.auth.recovery import decode_recovery_token
from backend.auth.models import User
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
logger = logging.getLogger(__name__)

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
    # OIDC nonce, generated at /claim/start and carried into try_complete_setup
    # so we can verify the id_token. Empty for OAuth2 providers that don't
    # issue id_tokens.
    nonce: str = ""


_SETUP_SESSIONS: dict[str, _SetupSession] = {}  # keyed by `state`
# Hard cap to bound memory if a pre-setup attacker spams /claim/start
# (the endpoint has no auth before the setup_completed sentinel is set —
# anyone who can reach the listener can mint a session). Far above any
# legitimate workload: only one operator runs the wizard at a time, and
# stale entries get swept on every call.
_SETUP_SESSIONS_MAX = 32


def _sweep_expired() -> None:
    """Drop expired entries from _SETUP_SESSIONS.

    Sessions are otherwise cleaned up only when accessed by state in the
    callback, so abandoned wizards would otherwise accumulate in memory.
    Called at the start of each /claim/start — O(n) over the small set
    of in-flight wizards, which is fine at single-deployment scale.
    """
    now = datetime.now(timezone.utc)
    expired = [state for state, s in _SETUP_SESSIONS.items() if now >= s.expires_at]
    for state in expired:
        del _SETUP_SESSIONS[state]


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


@setup_router.post(
    "/claim/start",
    # Pre-auth, pre-setup. _SETUP_SESSIONS_MAX caps memory; this caps
    # request rate. Together they make the unauth window safe.
    dependencies=[Depends(rate_limit("setup.claim_start", capacity=5, refill_per_sec=5 / 60))],
)
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

    _sweep_expired()

    # Hard cap to deny pre-auth memory-exhaustion DoS. After sweep, if the
    # live count is still at the cap, the attacker is the only thing keeping
    # entries fresh — refuse rather than evict legitimate in-flight wizards.
    if len(_SETUP_SESSIONS) >= _SETUP_SESSIONS_MAX:
        raise HTTPException(status_code=503, detail="Too many setup sessions in flight; try again shortly")

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
    # nonce binds the id_token to this specific wizard run. Only meaningful
    # for OIDC providers (fixed: google/microsoft, plus custom_oidc); OAuth2
    # providers don't issue id_tokens.
    is_oidc = (provider_config is not None and provider_config.protocol == "oidc") or provider_config is None
    nonce = secrets.token_urlsafe(32) if is_oidc else ""
    # Same callback URI as everyday sign-in (`/api/auth/callback/{slug}`).
    # The state lookup in _SETUP_SESSIONS is what tells that handler this
    # is a setup-completion flow vs. a normal sign-in. Reusing the URI means
    # operators register exactly one redirect URI at the IdP.
    redirect_uri = f"{body.app_base_url}/api/auth/callback/{body.oauth_slug}"
    params = {
        "client_id": body.oauth_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state,
    }
    if nonce:
        params["nonce"] = nonce
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
        nonce=nonce,
    )
    _SETUP_SESSIONS[state] = session

    return {"authorize_url": full_authorize_url}


# ---------------------------------------------------------------------------
# Setup-completion helper invoked from /api/auth/callback/{slug}
# ---------------------------------------------------------------------------


async def try_complete_setup(
    state: str,
    code: str,
    error: str,
    db: Session,
    app_settings: Settings,
):
    """If `state` matches a live setup session, finalize setup and return the response.

    Returns None when no setup is in flight for this state — the caller should
    fall through to the normal sign-in path. The state token is 32 bytes of
    `secrets.token_urlsafe`, so a collision with a normal sign-in state is
    negligible; the in-memory `_SETUP_SESSIONS` lookup IS the authentication
    here (same model the old /api/setup/claim/callback used).
    """
    if is_setup_completed(db):
        # No live setup possible. Fall through to normal sign-in.
        return None

    session = _SETUP_SESSIONS.get(state)
    if session is None:
        return None
    if datetime.now(timezone.utc) >= session.expires_at:
        del _SETUP_SESSIONS[state]
        return None

    # Provider sent an error
    if error:
        del _SETUP_SESSIONS[state]
        return _error_html("OAuth Error", f"The OAuth provider returned an error: {error}")

    # Determine token/userinfo URLs and OIDC issuer/jwks for id_token verify.
    slug = session.oauth_slug
    provider_config = FIXED_PROVIDERS.get(slug)
    oidc_issuer = ""
    oidc_jwks_uri = ""
    is_oidc = False
    if provider_config is not None:
        if provider_config.protocol == "oidc":
            discovery = await _get_discovery(provider_config.discovery_url)
            if discovery is None:
                del _SETUP_SESSIONS[state]
                return _error_html("OIDC Discovery Failed", "Could not fetch OIDC discovery document.")
            token_url = discovery.get("token_endpoint", "")
            userinfo_url = discovery.get("userinfo_endpoint", "")
            oidc_issuer = discovery.get("issuer", "")
            oidc_jwks_uri = discovery.get("jwks_uri", "")
            is_oidc = True
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
        oidc_issuer = discovery.get("issuer", "")
        oidc_jwks_uri = discovery.get("jwks_uri", "")
        is_oidc = True
        extract_subject = _oidc_extract_subject
        extract_name = _oidc_extract_name
        extract_email = _oidc_extract_email

    redirect_uri = f"{session.app_base_url}/api/auth/callback/{slug}"

    # SSRF-guard the discovery-derived URLs. Pre-setup this handler is
    # reachable unauthenticated, so a hostile issuer who controlled DNS
    # could otherwise point token/userinfo at internal addresses. Also
    # captures the resolved IPs so dns_pin locks each connection to the
    # exact IP the guard verified — closes the DNS-rebinding TOCTOU.
    import contextlib as _ctx

    from backend.auth.dns_pin import pin_dns
    from backend.auth.service import _ssrf_guard_discovery_url_async

    _pins: dict[str, str] = {}
    for url in (token_url, userinfo_url):
        try:
            _host, _ip = await _ssrf_guard_discovery_url_async(url)
            _pins[_host] = _ip
        except ValueError as exc:
            del _SETUP_SESSIONS[state]
            return _error_html(
                "Unsafe Provider URL",
                f"Provider endpoint refused by SSRF guard: {exc}",
            )

    try:
        with _ctx.ExitStack() as _stack:
            for _host, _ip in _pins.items():
                _stack.enter_context(pin_dns(_host, _ip))
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
                        token_data.get("error_description")
                        or token_data.get("error")
                        or "no access_token in response"
                    )
                    del _SETUP_SESSIONS[state]
                    return _error_html(
                        "Token Exchange Failed", f"Could not obtain access token: {err_desc}"
                    )

                # Verify id_token for OIDC providers (everything except GitHub
                # / Discord / Facebook). Anchors the IdP to its JWKS key and
                # binds the exchange to this wizard run via nonce — without
                # this, userinfo was our only authentication signal.
                if is_oidc:
                    id_token_value = token_data.get("id_token", "")
                    if not id_token_value or not oidc_jwks_uri:
                        del _SETUP_SESSIONS[state]
                        return _error_html(
                            "OIDC Verification Failed",
                            "Provider did not return an id_token; cannot complete setup.",
                        )
                    from backend.auth.oidc_verify import verify_id_token

                    claims = await verify_id_token(
                        id_token_value,
                        expected_issuer=oidc_issuer,
                        expected_audience=session.oauth_client_id,
                        expected_nonce=session.nonce,
                        jwks_uri=oidc_jwks_uri,
                        access_token=access_token,
                    )
                    if claims is None:
                        del _SETUP_SESSIONS[state]
                        return _error_html(
                            "OIDC Verification Failed",
                            "Could not verify id_token signature or claims. Check server logs.",
                        )

                userinfo_response = await client.get(
                    userinfo_url,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                userinfo = userinfo_response.json()
    except Exception as exc:
        # Log the full exception server-side; show a generic message to the
        # caller. Pre-setup this page is reachable unauthenticated, and
        # `exc` typically carries httpx connection details (target IPs,
        # internal hostnames if the issuer was misconfigured) that
        # shouldn't be echoed back to the browser.
        logger.exception("OAuth completion failed in setup wizard: %s", exc)
        del _SETUP_SESSIONS[state]
        return _error_html(
            "OAuth Error",
            "An error occurred during the OAuth flow. Check the server logs and try again.",
        )

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
            is_admin=True,
            is_pending=False,
            is_deleted=False,
        )
    )
    db.commit()
    mark_setup_completed(db)

    # Fetch the user object so create_access_token can read its fields.
    setup_user = db.get(User, session.default_net_control)
    # Issue JWT cookie and redirect.
    jwt_token = create_access_token(setup_user, app_settings)
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
