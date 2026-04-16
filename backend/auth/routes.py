from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session, get_settings, require_role
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.config import Settings

auth_router = APIRouter(tags=["auth"])


async def _get_oidc_client(settings: Settings) -> AsyncOAuth2Client:
    client = AsyncOAuth2Client(
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        redirect_uri=settings.oidc_redirect_uri,
    )
    return client


@auth_router.get("/login")
async def login(request: Request, app_settings: Settings = Depends(get_settings)):
    client = await _get_oidc_client(app_settings)
    authorization_url, state = await client.create_authorization_url(
        f"{app_settings.oidc_issuer_url}/authorize"
    )
    request.session_state = state
    return RedirectResponse(url=authorization_url)


@auth_router.get("/callback")
async def callback(
    request: Request,
    code: str,
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
):
    client = await _get_oidc_client(app_settings)
    token_response = await client.fetch_token(
        f"{app_settings.oidc_issuer_url}/token",
        code=code,
        grant_type="authorization_code",
    )

    userinfo = await client.get(f"{app_settings.oidc_issuer_url}/userinfo")
    userinfo_data = userinfo.json()

    oidc_subject = userinfo_data.get("sub", "")
    name = userinfo_data.get("name", userinfo_data.get("preferred_username", "Unknown"))

    # Look up existing user by OIDC subject
    user = db.query(User).filter(User.oidc_subject == oidc_subject).first()

    if user is None:
        # Check if this is the first user (auto-admin)
        user_count = db.query(User).count()
        role = UserRole.ADMIN if user_count == 0 else UserRole.VIEWER

        # Generate a placeholder callsign from the OIDC subject
        # User can update this later via profile
        callsign = userinfo_data.get(
            "preferred_username", oidc_subject[:20]
        ).upper()

        user = User(
            callsign=callsign,
            oidc_subject=oidc_subject,
            name=name,
            role=role,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    access_token = create_access_token(
        user.callsign, user.role.value, app_settings
    )
    response = RedirectResponse(url=app_settings.app_base_url)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
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
    }


@auth_router.post("/logout")
async def logout():
    response = Response(content='{"message": "logged out"}', media_type="application/json")
    response.delete_cookie(key="access_token")
    return response


class UserRoleUpdate(BaseModel):
    role: str


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
    target_user.role = UserRole(body.role)
    db.commit()
    db.refresh(target_user)
    return {
        "callsign": target_user.callsign,
        "name": target_user.name,
        "role": target_user.role.value,
    }
