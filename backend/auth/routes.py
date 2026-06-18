import re
import secrets
import urllib.parse

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session, get_settings, require_role
from backend.auth.rate_limit import rate_limit
from backend.auth.email import (
    notify_admins_new_registration,
    notify_admins_callsign_change,
    notify_user_approved,
    notify_user_callsign_approved,
)
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token, decode_access_token, resolve_provider
from backend.audit.service import log_action
from backend.config import Settings

auth_router = APIRouter(tags=["auth"])

CALLSIGN_PATTERN = re.compile(r"^[A-Z]{1,2}\d[A-Z]{1,4}$")


@auth_router.get("/providers")
async def list_providers(db: Session = Depends(get_db_session)):
    from backend.auth.providers import get_enabled_providers, build_providers
    enabled = get_enabled_providers(db)
    registry = build_providers(db)
    return [
        {"name": slug, "label": registry[slug].label}
        for slug in enabled
        if slug in registry
    ]


@auth_router.get(
    "/login/{provider}",
    # 10-request burst then ~10/min steady. Plenty for a human clicking
    # "Sign in" repeatedly; ceiling for a script.
    dependencies=[Depends(rate_limit("auth.login", capacity=10, refill_per_sec=10 / 60))],
)
async def login(
    provider: str,
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
):
    config = await resolve_provider(db, provider)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Unknown auth provider: {provider}")

    state = secrets.token_urlsafe(32)
    params = {
        "client_id": config["client_id"],
        "redirect_uri": f"{app_settings.app_base_url}/api/auth/callback/{provider}",
        "response_type": "code",
        "scope": config["scopes"],
        "state": state,
    }
    authorization_url = f"{config['authorize_url']}?{urllib.parse.urlencode(params)}"

    response = RedirectResponse(url=authorization_url)
    is_secure = app_settings.app_base_url.startswith("https://")
    response.set_cookie(
        key="oauth_state",
        value=f"{provider}:{state}",
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=600,
    )
    return response


@auth_router.get(
    "/callback/{provider}",
    # Same as /login. This is the route that creates PENDING users; the
    # registration_open gate plus this throttle bound DB-row spam.
    dependencies=[Depends(rate_limit("auth.callback", capacity=10, refill_per_sec=10 / 60))],
)
async def callback(
    provider: str,
    code: str = "",
    state: str = "",
    error: str = "",
    oauth_state: str | None = Cookie(default=None),
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
):
    # First-boot setup uses this same callback URL with a state token that
    # was minted by /api/setup/claim/start and stashed in _SETUP_SESSIONS.
    # If the state matches a live setup session, finalize setup here and
    # return; otherwise fall through to the everyday sign-in path. Reusing
    # the URI means the IdP only needs one redirect URI registered.
    from backend.config_mgmt.setup_routes import try_complete_setup

    setup_response = await try_complete_setup(state, code, error, db, app_settings)
    if setup_response is not None:
        return setup_response

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    expected_state = f"{provider}:{state}"
    if not oauth_state or not secrets.compare_digest(oauth_state, expected_state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    config = await resolve_provider(db, provider)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Unknown auth provider: {provider}")
    redirect_uri = f"{app_settings.app_base_url}/api/auth/callback/{provider}"

    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            config["token_url"],
            data={
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        token_data = token_response.json()
        access_token = token_data.get("access_token", "")

        if not access_token:
            raise HTTPException(status_code=400, detail="Failed to obtain access token from provider")

        userinfo_response = await client.get(
            config["userinfo_url"],
            headers={"Authorization": f"Bearer {access_token}"},
        )
        userinfo = userinfo_response.json()

    raw_subject = config["extract_subject"](userinfo)
    if not raw_subject:
        raise HTTPException(status_code=400, detail="Provider did not return a user identifier")

    oidc_subject = f"{provider}:{raw_subject}"
    name = config["extract_name"](userinfo)
    email = config["extract_email"](userinfo)

    user = db.query(User).filter(User.oidc_subject == oidc_subject).first()

    if user is None:
        # First user auto-becomes admin. Lock the users table so two
        # concurrent OAuth callbacks on PostgreSQL can't both see
        # count==0 and both insert as ADMIN. SQLite serializes writes
        # so this is a no-op there.
        user_count = db.query(User).with_for_update().count()

        # Registration gate. The first signup always wins (that's how the
        # admin claim works); subsequent signups require registration_open
        # to be true. Default is "true" (status quo); admins can flip it
        # from /config to stop drive-by OAuth flows from filling the DB
        # with PENDING rows. Reading via get_config_value avoids a hard
        # dependency on the AppConfig row existing.
        if user_count > 0:
            from backend.config_mgmt.service import get_config_value

            if get_config_value(db, "registration_open", "true").lower() != "true":
                raise HTTPException(
                    status_code=403,
                    detail="Registration is closed. Contact the net administrator.",
                )

        role = UserRole.ADMIN if user_count == 0 else UserRole.PENDING

        placeholder_callsign = f"PENDING-{oidc_subject[:12]}"

        user = User(
            callsign=placeholder_callsign,
            oidc_subject=oidc_subject,
            name=name,
            role=role,
            email=email or None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    jwt_token = create_access_token(user.callsign, user.role.value, app_settings, user.token_version)
    response = RedirectResponse(url=app_settings.app_base_url)
    is_secure = app_settings.app_base_url.startswith("https://")
    response.set_cookie(
        key="access_token",
        value=jwt_token,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=app_settings.jwt_expire_minutes * 60,
    )
    return response


@auth_router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {
        "callsign": user.callsign,
        "name": user.name,
        "role": user.role.value,
        "email": user.email,
        "pending_callsign": user.pending_callsign,
    }


@auth_router.post(
    "/logout",
    # Cheap, but bumps a DB row; throttle.
    dependencies=[Depends(rate_limit("auth.logout", capacity=30, refill_per_sec=30 / 60))],
)
async def logout(
    access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
):
    # Best-effort token_version bump so a stolen cookie can't outlive
    # logout. Decoded without invoking get_current_user (which would 401
    # an already-expired/invalidated token before we got here — making
    # repeat-logout a footgun); we just want the `sub` if it's there.
    if access_token:
        payload = decode_access_token(access_token, settings=app_settings)
        if payload is not None:
            callsign = payload.get("sub")
            if callsign:
                user = db.get(User, callsign)
                if user is not None:
                    user.token_version += 1
                    db.commit()
    response = Response(content='{"message": "logged out"}', media_type="application/json")
    response.delete_cookie(key="access_token", httponly=True, samesite="lax")
    return response


class RegisterRequest(BaseModel):
    callsign: str


@auth_router.post("/register")
async def register(
    body: RegisterRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    # Gate: only users who haven't claimed a real callsign yet may register.
    # The "PENDING-" prefix is what indicates "no real callsign chosen" — NOT
    # role==PENDING. The first-signup admin is created with role=ADMIN but a
    # PENDING- callsign (routes.py:141-143) and still needs this endpoint to
    # claim their real callsign without a self-approval round-trip. Don't be
    # tempted to also require role==PENDING; that breaks first-admin signup.
    if not user.callsign.startswith("PENDING-"):
        raise HTTPException(status_code=409, detail="User already registered")

    callsign = body.callsign.upper()
    if not CALLSIGN_PATTERN.match(callsign):
        raise HTTPException(status_code=400, detail="Invalid callsign format")

    existing = db.get(User, callsign)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Callsign already taken")

    old_callsign = user.callsign
    db.execute(
        sa.text("UPDATE users SET callsign = :new WHERE callsign = :old"),
        {"new": callsign, "old": old_callsign},
    )
    db.commit()

    user = db.get(User, callsign)

    # Notify admins (fire-and-forget)
    admins = db.query(User).filter(User.role == UserRole.ADMIN).all()
    await notify_admins_new_registration(db, admins, user)

    return {
        "callsign": user.callsign,
        "name": user.name,
        "role": user.role.value,
        "email": user.email,
        "pending_callsign": user.pending_callsign,
    }


class CallsignChangeRequest(BaseModel):
    callsign: str


@auth_router.patch("/me")
async def update_me(
    body: CallsignChangeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    callsign = body.callsign.upper()
    if not CALLSIGN_PATTERN.match(callsign):
        raise HTTPException(status_code=400, detail="Invalid callsign format")

    existing = db.get(User, callsign)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Callsign already taken")

    user.pending_callsign = callsign
    db.commit()
    db.refresh(user)

    # Notify admins (fire-and-forget)
    admins = db.query(User).filter(User.role == UserRole.ADMIN).all()
    await notify_admins_callsign_change(db, admins, user, callsign)

    return {
        "callsign": user.callsign,
        "name": user.name,
        "role": user.role.value,
        "email": user.email,
        "pending_callsign": user.pending_callsign,
    }


class UserRoleUpdate(BaseModel):
    role: UserRole


@auth_router.get("/users")
async def list_users(
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    users = db.query(User).order_by(User.callsign).all()
    return [
        {
            "callsign": u.callsign,
            "name": u.name,
            "role": u.role.value,
            "email": u.email,
            "pending_callsign": u.pending_callsign,
        }
        for u in users
    ]


@auth_router.patch("/users/{callsign}")
async def update_user_role(
    callsign: str,
    body: UserRoleUpdate,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    target_user = db.get(User, callsign)
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    old_role = target_user.role.value
    was_pending = target_user.role == UserRole.PENDING
    target_user.role = body.role
    # Bump token_version so any outstanding JWT for this user — which
    # encoded the old role — is rejected on next request. Without this,
    # a demoted admin keeps admin privileges until their JWT expires
    # (jwt_expire_minutes, default 1440 / 24 h).
    target_user.token_version += 1
    db.commit()
    db.refresh(target_user)
    log_action(
        db,
        actor=user.callsign,
        action="user.role_changed",
        target=callsign,
        details={"from": old_role, "to": body.role.value},
    )
    if was_pending and target_user.role != UserRole.PENDING:
        await notify_user_approved(db, target_user)
    return {
        "callsign": target_user.callsign,
        "name": target_user.name,
        "role": target_user.role.value,
        "email": target_user.email,
        "pending_callsign": target_user.pending_callsign,
    }


@auth_router.post("/users/{callsign}/approve-callsign")
async def approve_callsign(
    callsign: str,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    target_user = db.get(User, callsign)
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not target_user.pending_callsign:
        raise HTTPException(status_code=400, detail="No pending callsign change")

    new_callsign = target_user.pending_callsign

    existing = db.get(User, new_callsign)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Callsign already taken")

    db.execute(
        sa.text("UPDATE users SET callsign = :new, pending_callsign = NULL WHERE callsign = :old"),
        {"new": new_callsign, "old": callsign},
    )
    db.commit()
    log_action(
        db,
        actor=user.callsign,
        action="user.callsign_approved",
        target=new_callsign,
        details={"old": callsign, "new": new_callsign},
    )

    updated_user = db.get(User, new_callsign)
    await notify_user_callsign_approved(db, updated_user, callsign)
    return {
        "callsign": updated_user.callsign,
        "name": updated_user.name,
        "role": updated_user.role.value,
        "email": updated_user.email,
        "pending_callsign": updated_user.pending_callsign,
    }


@auth_router.delete("/users/{callsign}/pending-callsign")
async def reject_callsign(
    callsign: str,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    target_user = db.get(User, callsign)
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    pending = target_user.pending_callsign
    target_user.pending_callsign = None
    db.commit()
    log_action(db, actor=user.callsign, action="user.callsign_rejected", target=callsign, details={"pending": pending})
    return {"message": "Pending callsign change rejected"}
